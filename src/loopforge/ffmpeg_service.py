from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Sequence
from pathlib import Path

from .media_tools import MediaTools
from .models import ProcessResult

logger = logging.getLogger(__name__)


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


class ActiveProcess:
    def __init__(self, process: subprocess.Popen[str], args: tuple[str, ...]) -> None:
        self._process = process
        self.args = args
        self._lock = threading.Lock()
        self._result: ProcessResult | None = None

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def running(self) -> bool:
        return self._process.poll() is None

    def cancel(self) -> None:
        if self.running:
            self._process.terminate()

    def wait(self, timeout: float | None = None, check: bool = True) -> ProcessResult:
        with self._lock:
            if self._result is not None:
                return self._checked(self._result, check)
            try:
                stdout, stderr = self._process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as error:
                self._process.kill()
                stdout, stderr = self._process.communicate()
                result = ProcessResult(self.args, self._process.returncode, stdout, stderr)
                raise FFmpegTimeoutError(
                    f"Process timed out after {timeout} seconds", result
                ) from error
            self._result = ProcessResult(self.args, self._process.returncode, stdout, stderr)
            return self._checked(self._result, check)

    @staticmethod
    def _checked(result: ProcessResult, check: bool) -> ProcessResult:
        if check and result.returncode != 0:
            raise FFmpegError(f"Process failed with exit code {result.returncode}", result)
        return result


class FFmpegService:
    def __init__(self, tools: MediaTools) -> None:
        self.tools = tools

    def start(self, args: Sequence[str], *, probe: bool = False) -> ActiveProcess:
        executable = self.tools.ffprobe if probe else self.tools.ffmpeg
        command = (str(executable), *(str(arg) for arg in args))
        logger.info("Starting %s", Path(executable).name)
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
            )
        except FileNotFoundError as error:
            logger.error("Executable not found: %s", executable)
            raise FFmpegNotFoundError(f"Executable not found: {executable}") from error
        except OSError as error:
            logger.error("Unable to start %s: %s", Path(executable).name, error)
            raise FFmpegStartError(f"Unable to start {Path(executable).name}: {error}") from error
        logger.info("Started %s with PID %d", Path(executable).name, process.pid)
        return ActiveProcess(process, command)

    def run(
        self,
        args: Sequence[str],
        *,
        probe: bool = False,
        timeout: float | None = None,
        check: bool = True,
    ) -> ProcessResult:
        return self.start(args, probe=probe).wait(timeout, check)
