from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar, cast

from .media_tools import MediaTools
from .models import ProcessResult

logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class FFmpegProgress:
    frame: int | None = None
    out_time_us: int | None = None
    fps: float | None = None
    speed: float | None = None
    total_size: int | None = None
    ended: bool = False

    @property
    def out_time_seconds(self) -> float | None:
        return None if self.out_time_us is None else self.out_time_us / 1_000_000


class FFmpegProgressParser:
    def __init__(self) -> None:
        self._block: dict[str, str] = {}

    def feed(self, line: str) -> FFmpegProgress | None:
        line = line.strip()
        if not line:
            return None
        key, separator, value = line.partition("=")
        if not separator:
            return None
        self._block[key] = value
        if key != "progress":
            return None
        block, self._block = self._block, {}
        out_time = _integer(block.get("out_time_us"))
        if out_time is None and (text := block.get("out_time")):
            out_time = _timestamp_us(text)
        return FFmpegProgress(
            _integer(block.get("frame")),
            out_time,
            _float(block.get("fps")),
            _speed(block.get("speed")),
            _integer(block.get("total_size")),
            block.get("progress") == "end",
        )


class FFmpegError(RuntimeError):
    def __init__(self, message: str, result: ProcessResult | None = None) -> None:
        super().__init__(message)
        self.result = result


class FFmpegTimeoutError(FFmpegError):
    pass


class FFmpegStartError(FFmpegError):
    pass


class FFmpegNotFoundError(FFmpegStartError):
    pass


class ProcessCancelledError(FFmpegError):
    pass


Callback = Callable[[str], None]
ProgressCallback = Callable[[FFmpegProgress], None]
ProcessCallback = Callable[["ActiveProcess"], None]


class ActiveProcess:
    def __init__(
        self,
        process: subprocess.Popen[str],
        args: tuple[str, ...],
        *,
        streaming: bool = False,
        on_progress: ProgressCallback | None = None,
        on_log: Callback | None = None,
    ) -> None:
        self._process = process
        self.args = args
        self._streaming = streaming
        self._on_progress = on_progress
        self._on_log = on_log
        self._lock = threading.RLock()
        self._wait_lock = threading.Lock()
        self._result: ProcessResult | None = None
        self._cancelled = False

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def running(self) -> bool:
        return self._process.poll() is None

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def cancel(self, grace: float = 1.0) -> None:
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
            running = self.running
        if not running:
            return
        self._terminate_tree()
        try:
            self._process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            self._kill_tree()

    def _terminate_tree(self) -> None:
        try:
            if os.name == "nt":
                self._process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                killpg = cast(Callable[[int, int], None], getattr(os, "killpg", None))
                killpg(self.pid, signal.SIGTERM)
        except (OSError, ValueError):
            with suppress(OSError):
                self._process.terminate()

    def _kill_tree(self) -> None:
        try:
            if os.name == "nt":
                subprocess.run(
                    ("taskkill", "/PID", str(self.pid), "/T", "/F"),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                killpg = cast(Callable[[int, int], None], getattr(os, "killpg", None))
                sigkill = cast(int, getattr(signal, "SIGKILL", None))
                killpg(self.pid, sigkill)
        except (AttributeError, OSError):
            with suppress(OSError):
                self._process.kill()

    def wait(self, timeout: float | None = None, check: bool = True) -> ProcessResult:
        with self._wait_lock:
            if self._result is not None:
                return self._checked(self._result, check)
            if self._streaming:
                result = self._wait_streaming(timeout)
            else:
                result = self._wait_capture(timeout)
            self._result = result
            if self.cancelled and (check or self._streaming):
                raise ProcessCancelledError("Process cancelled", result)
            return self._checked(result, check)

    def _wait_capture(self, timeout: float | None) -> ProcessResult:
        try:
            stdout, stderr = self._process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            self._kill_tree()
            stdout, stderr = self._process.communicate()
            result = ProcessResult(self.args, self._process.returncode, stdout, stderr)
            raise FFmpegTimeoutError(
                f"Process timed out after {timeout} seconds", result
            ) from error
        return ProcessResult(self.args, self._process.returncode, stdout, stderr)

    def _wait_streaming(self, timeout: float | None) -> ProcessResult:
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        parser = FFmpegProgressParser()

        def drain_stdout() -> None:
            assert self._process.stdout is not None
            for line in self._process.stdout:
                stdout_lines.append(line)
                progress = parser.feed(line)
                if progress is not None:
                    _safe_call(self._on_progress, progress)

        def drain_stderr() -> None:
            assert self._process.stderr is not None
            for line in self._process.stderr:
                stderr_lines.append(line)
                _safe_call(self._on_log, line.rstrip())

        threads = (
            threading.Thread(target=drain_stdout, daemon=True),
            threading.Thread(target=drain_stderr, daemon=True),
        )
        for thread in threads:
            thread.start()
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            self._kill_tree()
            self._process.wait()
            for thread in threads:
                thread.join()
            result = ProcessResult(
                self.args, self._process.returncode, "".join(stdout_lines), "".join(stderr_lines)
            )
            raise FFmpegTimeoutError(
                f"Process timed out after {timeout} seconds", result
            ) from error
        for thread in threads:
            thread.join()
        return ProcessResult(
            self.args, self._process.returncode, "".join(stdout_lines), "".join(stderr_lines)
        )

    @staticmethod
    def _checked(result: ProcessResult, check: bool) -> ProcessResult:
        if check and result.returncode != 0:
            raise FFmpegError(f"Process failed with exit code {result.returncode}", result)
        return result


class FFmpegService:
    def __init__(self, tools: MediaTools) -> None:
        self.tools = tools

    def start(
        self,
        args: Sequence[str],
        *,
        probe: bool = False,
        streaming: bool = False,
        on_progress: ProgressCallback | None = None,
        on_log: Callback | None = None,
        on_process: ProcessCallback | None = None,
    ) -> ActiveProcess:
        executable = self.tools.ffprobe if probe else self.tools.ffmpeg
        command = (str(executable), *(str(arg) for arg in args))
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                creationflags=creationflags,
                start_new_session=os.name != "nt",
            )
        except FileNotFoundError as error:
            raise FFmpegNotFoundError(f"Executable not found: {executable}") from error
        except OSError as error:
            raise FFmpegStartError(f"Unable to start {Path(executable).name}: {error}") from error
        active = ActiveProcess(
            process, command, streaming=streaming, on_progress=on_progress, on_log=on_log
        )
        _safe_call(on_process, active)
        return active

    def run(
        self,
        args: Sequence[str],
        *,
        probe: bool = False,
        timeout: float | None = None,
        check: bool = True,
        streaming: bool = False,
        on_progress: ProgressCallback | None = None,
        on_log: Callback | None = None,
        on_process: ProcessCallback | None = None,
    ) -> ProcessResult:
        return self.start(
            args,
            probe=probe,
            streaming=streaming,
            on_progress=on_progress,
            on_log=on_log,
            on_process=on_process,
        ).wait(timeout, check)


def _safe_call(callback: Callable[[T], None] | None, value: T) -> None:
    if callback is None:
        return
    try:
        callback(value)
    except Exception:
        logger.exception("FFmpeg callback failed")


def _integer(value: str | None) -> int | None:
    try:
        return int(value) if value is not None and value != "N/A" else None
    except ValueError:
        return None


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None and value != "N/A" else None
    except ValueError:
        return None


def _speed(value: str | None) -> float | None:
    return _float(value[:-1]) if value and value.endswith("x") else _float(value)


def _timestamp_us(value: str) -> int | None:
    try:
        hours, minutes, seconds = value.split(":")
        return int((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1_000_000)
    except (ValueError, TypeError):
        return None
