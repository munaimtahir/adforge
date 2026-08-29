from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from adforge.audio import LocalProceduralMusicProvider, MusicRequest
from adforge.renderer import EditSpec, FFmpegRenderer, RenderError
from adforge.storage import UnsafePathError


def create_video(path: Path, duration: int = 6) -> None:
    subprocess.run(  # noqa: S603, S607 - test fixture uses fixed ffmpeg argv
        [
            "/usr/bin/ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=#202428:s=360x640:d={duration}:r=30",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )


def fixture_spec(text: str = "Keep your proof organized") -> EditSpec:
    return EditSpec.model_validate(
        {
            "campaign_id": "fixture",
            "clips": [
                {
                    "source": "generated/fixture.mp4",
                    "timeline_start_seconds": 0,
                    "source_in_seconds": 0,
                    "source_out_seconds": 6,
                    "transition_in": {"type": "FADE", "duration_seconds": 0.2},
                    "transition_out": {"type": "FADE", "duration_seconds": 0.2},
                }
            ],
            "overlays": [
                {
                    "text": text,
                    "start_seconds": 0.5,
                    "end_seconds": 4,
                    "position": "CENTER",
                    "font_size": 30,
                    "background": True,
                }
            ],
            "captions": [
                {
                    "text": "A deterministic caption",
                    "start_seconds": 1,
                    "end_seconds": 3,
                    "position": "BOTTOM",
                    "font_size": 20,
                }
            ],
            "audio_tracks": [
                {
                    "source": "audio/music.wav",
                    "kind": "MUSIC",
                    "gain": 0.5,
                    "duck_under_narration": True,
                }
            ],
            "cta": {
                "text": "Learn more",
                "start_seconds": 4,
                "end_seconds": 6,
                "position": "BOTTOM",
                "font_size": 28,
            },
            "output_profile": {
                "aspect_ratio": "9:16",
                "duration_seconds": 6,
                "width": 360,
                "height": 640,
            },
            "output_path": "renders/final/fixture.mp4",
        }
    )


def test_device_frame_composition_renders_a_real_mp4(tmp_path: Path) -> None:
    workspace = tmp_path / "campaign"
    (workspace / "generated").mkdir(parents=True)
    create_video(workspace / "generated" / "fixture.mp4")
    spec = EditSpec.model_validate(
        {
            "campaign_id": "fixture",
            "clips": [
                {
                    "source": "generated/fixture.mp4",
                    "timeline_start_seconds": 0,
                    "source_in_seconds": 0,
                    "source_out_seconds": 6,
                    "composition_mode": "DEVICE_FRAME",
                    "device_frame_scale": 0.7,
                    "device_frame_background": "#101014",
                    "device_frame_shadow": True,
                    "device_frame_corner_radius": 60,
                }
            ],
            "overlays": [
                {
                    "text": "Real DEVICE_FRAME compositing",
                    "start_seconds": 0.5,
                    "end_seconds": 4,
                    "position": "TOP",
                    "alignment": "left",
                    "font_size": 26,
                }
            ],
            "output_profile": {
                "aspect_ratio": "9:16",
                "duration_seconds": 6,
                "width": 360,
                "height": 640,
            },
            "output_path": "renders/final/device-frame.mp4",
        }
    )
    result = FFmpegRenderer().render(spec, workspace)
    assert result.codec == "h264"
    assert (result.width, result.height) == (360, 640)
    assert result.duration_seconds == pytest.approx(6, abs=0.1)
    assert result.output_path.is_file()


def test_deterministic_fixture_renders_valid_mp4_with_audio(tmp_path: Path) -> None:
    workspace = tmp_path / "campaign"
    (workspace / "generated").mkdir(parents=True)
    (workspace / "audio").mkdir()
    create_video(workspace / "generated" / "fixture.mp4")
    LocalProceduralMusicProvider().generate(
        MusicRequest(duration_seconds=6), workspace / "audio" / "music.wav"
    )
    result = FFmpegRenderer().render(fixture_spec(), workspace)
    assert result.codec == "h264"
    assert (result.width, result.height) == (360, 640)
    assert result.duration_seconds == pytest.approx(6, abs=0.1)
    assert result.has_audio is True
    assert result.output_path.is_file()


@pytest.mark.parametrize("ratio", ["9:16", "16:9", "1:1"])
@pytest.mark.parametrize("duration", [6, 10, 15, 20, 30])
def test_all_required_profiles_are_accepted(ratio: str, duration: int) -> None:
    width, height = {"9:16": (360, 640), "16:9": (640, 360), "1:1": (480, 480)}[
        ratio
    ]
    spec = fixture_spec().model_dump(mode="python")
    spec["clips"][0]["source_out_seconds"] = duration
    spec["output_profile"].update(
        {"aspect_ratio": ratio, "duration_seconds": duration, "width": width, "height": height}
    )
    assert EditSpec.model_validate(spec).output_profile.duration_seconds == duration


def test_invalid_specs_and_paths_fail_safely(tmp_path: Path) -> None:
    invalid = fixture_spec().model_dump(mode="python")
    invalid["clips"][0]["source_out_seconds"] = 5
    with pytest.raises(ValidationError, match="fill output"):
        EditSpec.model_validate(invalid)
    workspace = tmp_path / "campaign"
    workspace.mkdir()
    unsafe = fixture_spec().model_copy(update={"output_path": "../escape.mp4"})
    with pytest.raises(UnsafePathError):
        FFmpegRenderer().render(unsafe, workspace)


def test_text_cannot_inject_shell_or_filter_graph(tmp_path: Path) -> None:
    workspace = tmp_path / "campaign"
    (workspace / "generated").mkdir(parents=True)
    (workspace / "audio").mkdir()
    create_video(workspace / "generated" / "fixture.mp4")
    LocalProceduralMusicProvider().generate(
        MusicRequest(duration_seconds=6), workspace / "audio" / "music.wav"
    )
    sentinel = tmp_path / "pwned"
    text = "$(touch pwned)';[0:v]null; <safe>"
    result = FFmpegRenderer().render(fixture_spec(text), workspace)
    assert result.output_path.is_file()
    assert not sentinel.exists()


def test_renderer_reports_missing_source(tmp_path: Path) -> None:
    workspace = tmp_path / "campaign"
    workspace.mkdir()
    with pytest.raises(RenderError, match="source asset is missing"):
        FFmpegRenderer().render(fixture_spec(), workspace)


def test_probe_reports_a_clear_error_instead_of_a_keyerror_when_duration_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: found live on a real Android screen recording.

    `adb shell screenrecord` can produce a well-formed MP4 container with a
    valid video stream but no format-level duration atom (e.g. a truncated
    capture with a single keyframe). ffprobe parses it without error, so the
    old code crashed with a bare `KeyError: 'duration'` deep inside EDIT_PLAN
    instead of a diagnosable error.
    """
    clip = tmp_path / "clip.mp4"
    create_video(clip, duration=1)

    real_run = subprocess.run

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[0].endswith("ffprobe"):
            result = real_run(command, **kwargs)  # type: ignore[arg-type]
            payload = json.loads(result.stdout)
            del payload["format"]["duration"]
            return subprocess.CompletedProcess(
                command, result.returncode, json.dumps(payload), result.stderr
            )
        return real_run(command, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RenderError, match="could not determine a duration"):
        FFmpegRenderer().probe(clip, expect_audio=False)
