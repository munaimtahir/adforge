"""Typed edit specification and canonical safe FFmpeg renderer adapter."""

from __future__ import annotations

import json
import shutil
import subprocess
from abc import ABC, abstractmethod
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from adforge.storage import UnsafePathError, safe_component, sha256_file

SUPPORTED_DURATIONS = {6, 10, 15, 20, 30}
RATIO_DIMENSIONS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}


class RenderError(RuntimeError):
    pass


class FitMode(StrEnum):
    COVER = "COVER"
    CONTAIN = "CONTAIN"


class TransitionSpec(BaseModel):
    type: Literal["CUT", "FADE"] = "CUT"
    duration_seconds: float = Field(default=0, ge=0, le=1)


class ClipSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    timeline_start_seconds: float = Field(ge=0)
    source_in_seconds: float = Field(default=0, ge=0)
    source_out_seconds: float = Field(gt=0)
    fit: FitMode = FitMode.COVER
    transition_in: TransitionSpec = Field(default_factory=TransitionSpec)
    transition_out: TransitionSpec = Field(default_factory=TransitionSpec)

    @model_validator(mode="after")
    def valid_trim(self) -> ClipSpec:
        if self.source_out_seconds <= self.source_in_seconds:
            raise ValueError("clip source_out must be after source_in")
        return self

    @property
    def duration_seconds(self) -> float:
        return self.source_out_seconds - self.source_in_seconds


class TextOverlay(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    position: Literal["TOP", "CENTER", "BOTTOM"] = "CENTER"
    font_size: int = Field(default=64, ge=12, le=180)
    color: str = Field(default="white", pattern=r"^[A-Za-z0-9#]{3,20}$")
    background: bool = False

    @model_validator(mode="after")
    def valid_interval(self) -> TextOverlay:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("text overlay end must be after start")
        if "\x00" in self.text:
            raise ValueError("text overlay contains NUL")
        return self


class LogoOverlay(BaseModel):
    source: str
    start_seconds: float = Field(default=0, ge=0)
    end_seconds: float = Field(gt=0)
    width: int = Field(default=180, ge=16, le=800)
    position: Literal["TOP_LEFT", "TOP_RIGHT", "BOTTOM_LEFT", "BOTTOM_RIGHT"] = "TOP_RIGHT"


class AudioTrackSpec(BaseModel):
    source: str
    kind: Literal["NARRATION", "MUSIC", "SFX"]
    start_seconds: float = Field(default=0, ge=0)
    source_in_seconds: float = Field(default=0, ge=0)
    source_out_seconds: float | None = Field(default=None, gt=0)
    gain: float = Field(default=1, ge=0, le=4)
    duck_under_narration: bool = False


class OutputProfile(BaseModel):
    aspect_ratio: Literal["9:16", "16:9", "1:1"]
    duration_seconds: int
    width: int | None = Field(default=None, ge=240, le=3840)
    height: int | None = Field(default=None, ge=240, le=3840)
    fps: int = Field(default=30, ge=24, le=60)
    video_codec: Literal["libx264"] = "libx264"
    audio_codec: Literal["aac"] = "aac"
    export_kind: Literal["MASTER", "DELIVERY"] = "MASTER"
    crf: int = Field(default=18, ge=0, le=51)
    preset: str = Field(default="medium", pattern=r"^[a-z0-9-]+$")
    audio_bitrate: str = Field(default="192k", pattern=r"^[0-9]+k$")
    faststart: bool = True

    @model_validator(mode="after")
    def supported_profile(self) -> OutputProfile:
        if self.duration_seconds not in SUPPORTED_DURATIONS:
            raise ValueError("unsupported output duration")
        default_width, default_height = RATIO_DIMENSIONS[self.aspect_ratio]
        width = self.width or default_width
        height = self.height or default_height
        expected = default_width / default_height
        if abs(width / height - expected) > 0.01:
            raise ValueError("dimensions do not match aspect ratio")
        self.width = width
        self.height = height
        return self


class EditSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: str
    clips: list[ClipSpec] = Field(min_length=1)
    overlays: list[TextOverlay] = Field(default_factory=list)
    captions: list[TextOverlay] = Field(default_factory=list)
    logo: LogoOverlay | None = None
    audio_tracks: list[AudioTrackSpec] = Field(default_factory=list)
    cta: TextOverlay | None = None
    output_profile: OutputProfile
    output_path: str

    @model_validator(mode="after")
    def timeline_is_contiguous(self) -> EditSpec:
        clips = sorted(self.clips, key=lambda item: item.timeline_start_seconds)
        if abs(clips[0].timeline_start_seconds) > 0.01:
            raise ValueError("clip timeline must start at zero")
        cursor = 0.0
        for clip in clips:
            if abs(clip.timeline_start_seconds - cursor) > 0.01:
                raise ValueError("clip timeline contains a gap or overlap")
            cursor += clip.duration_seconds
        if abs(cursor - self.output_profile.duration_seconds) > 0.05:
            raise ValueError("clips do not fill output duration")
        return self


class RenderResult(BaseModel):
    output_path: Path
    checksum: str
    codec: str
    width: int
    height: int
    duration_seconds: float
    has_audio: bool
    ffprobe: dict[str, Any]


class Renderer(ABC):
    @abstractmethod
    def render(self, spec: EditSpec, workspace: Path) -> RenderResult: ...


class FFmpegRenderer(Renderer):
    def __init__(
        self,
        ffmpeg: str | None = None,
        ffprobe: str | None = None,
        font_path: Path | None = None,
    ) -> None:
        self.ffmpeg = self._executable(ffmpeg or shutil.which("ffmpeg"), "ffmpeg")
        self.ffprobe = self._executable(ffprobe or shutil.which("ffprobe"), "ffprobe")
        self.font_path = (
            font_path or Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        ).resolve()
        if not self.font_path.is_file():
            raise RenderError("deterministic font file is unavailable")

    def render(self, spec: EditSpec, workspace: Path) -> RenderResult:
        workspace = workspace.resolve()
        output = self._path(workspace, spec.output_path, require_file=False)
        if output.suffix.lower() != ".mp4":
            raise RenderError("render output must use .mp4")
        output.parent.mkdir(parents=True, exist_ok=True)
        build_root = workspace / "temp" / f"render-{safe_component(output.stem)}"
        build_root.mkdir(parents=True, exist_ok=True)
        command = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        clip_paths = [self._path(workspace, clip.source) for clip in spec.clips]
        audio_paths = [self._path(workspace, track.source) for track in spec.audio_tracks]
        logo_path = self._path(workspace, spec.logo.source) if spec.logo else None
        for path in clip_paths:
            command.extend(["-i", str(path)])
        for path in audio_paths:
            command.extend(["-i", str(path)])
        logo_index: int | None = None
        if logo_path:
            logo_index = len(clip_paths) + len(audio_paths)
            command.extend(["-loop", "1", "-i", str(logo_path)])
        filters, video_label, audio_label = self._filters(
            spec, build_root, len(clip_paths), logo_index
        )
        command.extend(["-filter_complex", ";".join(filters), "-map", f"[{video_label}]"])
        if audio_label:
            command.extend(["-map", f"[{audio_label}]"])
        command.extend(
            [
                "-t",
                str(spec.output_profile.duration_seconds),
                "-r",
                str(spec.output_profile.fps),
                "-c:v",
                spec.output_profile.video_codec,
                "-pix_fmt",
                "yuv420p",
                "-preset",
                spec.output_profile.preset,
                "-crf",
                str(spec.output_profile.crf),
            ]
        )
        if spec.output_profile.faststart:
            command.extend(["-movflags", "+faststart"])
        if audio_label:
            command.extend(
                ["-c:a", spec.output_profile.audio_codec, "-b:a", spec.output_profile.audio_bitrate]
            )
        command.append(str(output))
        result = subprocess.run(  # noqa: S603 - fixed executable/argv; no shell
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=max(120, spec.output_profile.duration_seconds * 20),
        )
        if result.returncode != 0:
            raise RenderError(f"FFmpeg render failed: {result.stderr[-2000:]}")
        return self.probe(output, expect_audio=bool(spec.audio_tracks))

    def probe(self, output: Path, *, expect_audio: bool) -> RenderResult:
        result = subprocess.run(  # noqa: S603 - fixed executable/argv; no shell
            [
                self.ffprobe,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(output),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RenderError(f"ffprobe failed: {result.stderr[-1000:]}")
        data: dict[str, Any] = json.loads(result.stdout)
        video = next((item for item in data["streams"] if item["codec_type"] == "video"), None)
        audio = next((item for item in data["streams"] if item["codec_type"] == "audio"), None)
        if video is None or (expect_audio and audio is None):
            raise RenderError("render is missing required video/audio streams")
        duration = data["format"].get("duration")
        if duration is None:
            raise RenderError(
                f"ffprobe could not determine a duration for {output} -- the file's "
                "moov/duration atom is missing or unset, which usually means the "
                "source video was truncated or never finalized (e.g. a device "
                "recording pulled before it stopped writing)"
            )
        return RenderResult(
            output_path=output,
            checksum=sha256_file(output),
            codec=video["codec_name"],
            width=int(video["width"]),
            height=int(video["height"]),
            duration_seconds=float(duration),
            has_audio=audio is not None,
            ffprobe=data,
        )

    def _filters(
        self,
        spec: EditSpec,
        build_root: Path,
        clip_count: int,
        logo_index: int | None,
    ) -> tuple[list[str], str, str | None]:
        width = spec.output_profile.width
        height = spec.output_profile.height
        assert width is not None and height is not None
        filters: list[str] = []
        ordered = sorted(spec.clips, key=lambda item: item.timeline_start_seconds)
        for index, clip in enumerate(ordered):
            if clip.fit == FitMode.COVER:
                geometry = (
                    f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height}"
                )
            else:
                geometry = (
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
                )
            effects: list[str] = []
            if clip.transition_in.type == "FADE" and clip.transition_in.duration_seconds:
                effects.append(f"fade=t=in:st=0:d={clip.transition_in.duration_seconds}")
            if clip.transition_out.type == "FADE" and clip.transition_out.duration_seconds:
                start = max(0, clip.duration_seconds - clip.transition_out.duration_seconds)
                effects.append(f"fade=t=out:st={start}:d={clip.transition_out.duration_seconds}")
            suffix = "," + ",".join(effects) if effects else ""
            filters.append(
                f"[{index}:v]trim=start={clip.source_in_seconds}:end={clip.source_out_seconds},"
                f"setpts=PTS-STARTPTS,{geometry},setsar=1,fps={spec.output_profile.fps},"
                f"format=yuv420p{suffix}[v{index}]"
            )
        joined = "".join(f"[v{index}]" for index in range(clip_count))
        filters.append(f"{joined}concat=n={clip_count}:v=1:a=0[vbase]")
        video_label = "vbase"
        text_items = [*spec.overlays, *spec.captions]
        if spec.cta:
            text_items.append(spec.cta)
        for index, overlay in enumerate(text_items):
            text_path = build_root / f"text-{index}.txt"
            text_path.write_text(overlay.text)
            next_label = f"vtext{index}"
            y = {"TOP": "h*0.12", "CENTER": "(h-text_h)/2", "BOTTOM": "h*0.82"}[overlay.position]
            box = ":box=1:boxcolor=black@0.65:boxborderw=18" if overlay.background else ""
            filters.append(
                f"[{video_label}]drawtext=fontfile='{self._escape(self.font_path)}':"
                f"textfile='{self._escape(text_path)}':fontcolor={overlay.color}:"
                f"fontsize={overlay.font_size}:x=(w-text_w)/2:y={y}{box}:"
                f"enable='between(t,{overlay.start_seconds},{overlay.end_seconds})'"
                f"[{next_label}]"
            )
            video_label = next_label
        if spec.logo and logo_index is not None:
            filters.append(f"[{logo_index}:v]scale={spec.logo.width}:-1[logo]")
            positions = {
                "TOP_LEFT": ("24", "24"),
                "TOP_RIGHT": ("W-w-24", "24"),
                "BOTTOM_LEFT": ("24", "H-h-24"),
                "BOTTOM_RIGHT": ("W-w-24", "H-h-24"),
            }
            x, y = positions[spec.logo.position]
            filters.append(
                f"[{video_label}][logo]overlay=x={x}:y={y}:"
                f"enable='between(t,{spec.logo.start_seconds},{spec.logo.end_seconds})'"
                "[vlogo]"
            )
            video_label = "vlogo"
        audio_labels: list[str] = []
        for index, track in enumerate(spec.audio_tracks):
            input_index = clip_count + index
            end = f":end={track.source_out_seconds}" if track.source_out_seconds else ""
            effective_gain = track.gain * (0.35 if track.duck_under_narration else 1)
            delay = round(track.start_seconds * 1000)
            label = f"a{index}"
            filters.append(
                f"[{input_index}:a]atrim=start={track.source_in_seconds}{end},"
                f"asetpts=PTS-STARTPTS,volume={effective_gain},adelay={delay}|{delay}[{label}]"
            )
            audio_labels.append(label)
        audio_label: str | None = None
        if audio_labels:
            joined_audio = "".join(f"[{label}]" for label in audio_labels)
            filters.append(
                f"{joined_audio}amix=inputs={len(audio_labels)}:duration=longest:"
                "dropout_transition=0,alimiter=limit=0.95[aout]"
            )
            audio_label = "aout"
        return filters, video_label, audio_label

    @staticmethod
    def _path(workspace: Path, relative: str, *, require_file: bool = True) -> Path:
        path = Path(relative)
        if path.is_absolute() or not path.parts:
            raise UnsafePathError("edit spec path must be relative")
        for part in path.parts:
            safe_component(part)
        resolved = (workspace / path).resolve()
        if not resolved.is_relative_to(workspace):
            raise UnsafePathError("edit spec path escapes campaign workspace")
        if require_file and not resolved.is_file():
            raise RenderError(f"source asset is missing: {relative}")
        return resolved

    @staticmethod
    def _escape(path: Path) -> str:
        return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

    @staticmethod
    def _executable(value: str | None, name: str) -> str:
        if value is None or not Path(value).is_file():
            raise RenderError(f"{name} executable is unavailable")
        return str(Path(value).resolve())
