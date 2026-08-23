import subprocess
import sys
from pathlib import Path
from subprocess import Popen
from typing import cast

import pytest

from loopforge.ffmpeg_service import FFmpegError, FFmpegService, FFmpegTimeoutError
from loopforge.media_tools import MediaTools


def python_service() -> FFmpegService:
    executable = Path(sys.executable)
    return FFmpegService(MediaTools(executable, executable, "python", "python"))


def test_run_captures_output_and_structured_failure() -> None:
    service = python_service()
    result = service.run(["-c", "print('ok')"])
    assert result.stdout.strip() == "ok"
    assert result.args[0] == sys.executable

    with pytest.raises(FFmpegError) as caught:
        service.run(["-c", "import sys; print('bad', file=sys.stderr); sys.exit(3)"])
    assert caught.value.result is not None
    assert caught.value.result.returncode == 3
    assert "bad" in caught.value.result.stderr


def test_active_process_cancel_and_timeout() -> None:
    service = python_service()
    active = service.start(["-c", "import time; time.sleep(10)"])
    assert active.running
    active.cancel()
    assert active.wait(check=False).returncode != 0

    with pytest.raises(FFmpegTimeoutError) as caught:
        service.run(["-c", "import time; time.sleep(10)"], timeout=0.05)
    assert caught.value.result is not None


def test_start_does_not_use_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    original = subprocess.Popen
    seen: dict[str, object] = {}

    def popen(*args: object, **kwargs: object) -> Popen[str]:
        seen.update(kwargs)
        return cast(Popen[str], original(*args, **kwargs))  # type: ignore[call-overload]

    monkeypatch.setattr(subprocess, "Popen", popen)
    python_service().run(["-c", "pass"])
    assert seen["shell"] is False
