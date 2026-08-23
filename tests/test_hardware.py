from loopforge.hardware import HardwareDetector, parse_video_encoders
from loopforge.models import ProcessResult


class FakeFFmpeg:
    def __init__(self) -> None:
        self.calls = 0

    def run(
        self,
        args: list[str],
        *,
        probe: bool = False,
        timeout: float | None = None,
        check: bool = True,
    ) -> ProcessResult:
        self.calls += 1
        return ProcessResult(
            tuple(args),
            0,
            """Encoders:
 V..... libx264              H.264
 V....D h264_nvenc           NVIDIA NVENC H.264
 V..... hevc_qsv             HEVC Intel Quick Sync
 V..... hevc_amf             AMD AMF HEVC
 V..... libsvtav1            SVT-AV1
 A..... aac                  AAC
 V..... vp9_qsv              VP9
""",
            "",
        )


def test_parse_video_encoders_classifies_supported_codecs_and_backends() -> None:
    capabilities = parse_video_encoders(
        " V..... libx264 H.264\n V....D h264_nvenc NVIDIA\n V..... hevc_qsv Intel\n"
        " V..... hevc_amf AMD\n V..... libaom-av1 AV1\n A..... aac AAC"
    )

    assert [(item.codec, item.backend) for item in capabilities.encoders] == [
        ("h264", "cpu"),
        ("h264", "nvenc"),
        ("hevc", "qsv"),
        ("hevc", "amf"),
        ("av1", "cpu"),
    ]
    assert capabilities.supports("h264", "nvenc")
    assert not capabilities.supports("av1", "qsv")
    assert capabilities.available_encoders is capabilities.encoders
    assert capabilities.has_nvenc and capabilities.has_qsv and capabilities.has_amf
    assert [item.name for item in capabilities.encoders_for("h264", "nvenc")] == ["h264_nvenc"]
    assert capabilities.encoders_for("h264", "nvenc")[0].hardware
    assert not capabilities.encoders_for("h264", "cpu")[0].hardware


def test_detector_caches_and_refreshes() -> None:
    ffmpeg = FakeFFmpeg()
    detector = HardwareDetector(ffmpeg)  # type: ignore[arg-type]

    first = detector.detect()
    assert detector.detect() is first
    detector.detect(refresh=True)
    assert ffmpeg.calls == 2
