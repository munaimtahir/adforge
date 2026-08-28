"""Replaceable audio providers, legal local fallbacks, validation, and mixing."""

from __future__ import annotations

import math
import wave
from abc import ABC, abstractmethod
from array import array
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from adforge.models import Asset, LedgerEvent
from adforge.services import Services
from adforge.storage import sha256_file

SAMPLE_RATE = 48_000
MAX_SAMPLE = 32_767


class AudioError(RuntimeError):
    pass


class VoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=10_000)
    language: str = "en"
    voice: str = "neutral"
    target_duration_seconds: float | None = Field(default=None, gt=0, le=300)
    clone_reference: Path | None = None
    clone_authorized: bool = False
    authorization_reference: str | None = None

    @model_validator(mode="after")
    def cloning_requires_authorization(self) -> VoiceRequest:
        if self.clone_reference is not None and (
            not self.clone_authorized or not self.authorization_reference
        ):
            raise ValueError("voice cloning requires explicit authorization and provenance")
        return self


class MusicRequest(BaseModel):
    duration_seconds: float = Field(gt=0, le=300)
    mood: str = "restrained optimistic"
    license_reference: str = "AdForge local procedural fallback; original synthesis"


class AudioMetadata(BaseModel):
    path: Path
    duration_seconds: float
    sample_rate: int
    channels: int
    peak: float = Field(ge=0, le=1)
    rms_dbfs: float
    provider: str
    provenance: dict[str, str]


class VoiceProvider(ABC):
    @abstractmethod
    def synthesize(self, request: VoiceRequest, destination: Path) -> AudioMetadata: ...


class MusicProvider(ABC):
    @abstractmethod
    def generate(self, request: MusicRequest, destination: Path) -> AudioMetadata: ...


def write_tone_sequence(
    destination: Path,
    duration_seconds: float,
    frequencies: Sequence[float],
    *,
    amplitude: float,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame_count = int(duration_seconds * SAMPLE_RATE)
    samples = array("h")
    segment = max(1, frame_count // max(1, len(frequencies)))
    fade_frames = int(0.01 * SAMPLE_RATE)
    for index in range(frame_count):
        frequency = frequencies[min(index // segment, len(frequencies) - 1)]
        within = index % segment
        envelope = min(1.0, within / fade_frames, (segment - within) / fade_frames)
        sample = math.sin(2 * math.pi * frequency * index / SAMPLE_RATE)
        samples.append(int(MAX_SAMPLE * amplitude * max(0, envelope) * sample))
    with wave.open(str(destination), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(samples.tobytes())


class LocalProceduralVoiceProvider(VoiceProvider):
    """Offline, original waveform fallback for contract and timing tests."""

    name = "local-procedural-voice"

    def synthesize(self, request: VoiceRequest, destination: Path) -> AudioMetadata:
        words = max(1, len(request.text.split()))
        duration = request.target_duration_seconds or max(0.8, words / 2.5)
        frequencies = [
            180 + (ord(character) % 12) * 12
            for character in request.text
            if character.isalpha()
        ]
        write_tone_sequence(destination, duration, frequencies or [220], amplitude=0.32)
        metadata = validate_wav(destination, provider=self.name)
        metadata.provenance = {
            "type": "original procedural test fallback",
            "voice": request.voice,
            "language": request.language,
            "clone_authorized": str(request.clone_authorized).lower(),
            "authorization_reference": request.authorization_reference or "not-applicable",
        }
        return metadata


class LocalProceduralMusicProvider(MusicProvider):
    name = "local-procedural-music"

    def generate(self, request: MusicRequest, destination: Path) -> AudioMetadata:
        write_tone_sequence(
            destination,
            request.duration_seconds,
            [220.0, 277.18, 329.63, 277.18],
            amplitude=0.16,
        )
        metadata = validate_wav(destination, provider=self.name)
        metadata.provenance = {
            "type": "original procedural test fallback",
            "mood": request.mood,
            "license": request.license_reference,
        }
        return metadata


def generate_local_sfx(destination: Path, *, duration_seconds: float = 0.12) -> AudioMetadata:
    write_tone_sequence(destination, duration_seconds, [880, 660], amplitude=0.25)
    metadata = validate_wav(destination, provider="local-procedural-sfx")
    metadata.provenance = {
        "type": "original procedural test fallback",
        "license": "generated locally by AdForge",
    }
    return metadata


def validate_wav(path: Path, *, provider: str = "unknown") -> AudioMetadata:
    try:
        with wave.open(str(path), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            frames = source.getnframes()
            raw = source.readframes(frames)
    except (wave.Error, OSError) as exc:
        raise AudioError(f"invalid WAV: {path.name}") from exc
    if channels not in {1, 2} or sample_width != 2 or sample_rate < 16_000 or frames == 0:
        raise AudioError("WAV must be non-empty 16-bit mono/stereo audio at >=16kHz")
    samples = array("h")
    samples.frombytes(raw)
    if not samples:
        raise AudioError("WAV contains no samples")
    peak = max(abs(sample) for sample in samples) / MAX_SAMPLE
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / MAX_SAMPLE
    rms_dbfs = 20 * math.log10(max(rms, 1e-12))
    return AudioMetadata(
        path=path,
        duration_seconds=frames / sample_rate,
        sample_rate=sample_rate,
        channels=channels,
        peak=peak,
        rms_dbfs=rms_dbfs,
        provider=provider,
        provenance={},
    )


def mix_wav(
    narration: Path,
    music: Path,
    destination: Path,
    *,
    narration_gain: float = 1.0,
    music_gain: float = 0.22,
) -> AudioMetadata:
    narration_samples, sample_rate = _read_mono(narration)
    music_samples, music_rate = _read_mono(music)
    if sample_rate != music_rate:
        raise AudioError("mix inputs must have the same sample rate")
    frame_count = max(len(narration_samples), len(music_samples))
    mixed = array("h")
    for index in range(frame_count):
        voice = narration_samples[index] if index < len(narration_samples) else 0
        bed = music_samples[index % len(music_samples)] if music_samples else 0
        value = voice * narration_gain + bed * music_gain
        mixed.append(round(max(-0.95 * MAX_SAMPLE, min(0.95 * MAX_SAMPLE, value))))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(destination), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(mixed.tobytes())
    metadata = validate_wav(destination, provider="adforge-local-mixer")
    metadata.provenance = {
        "narration": sha256_file(narration),
        "music": sha256_file(music),
        "narration_gain": str(narration_gain),
        "music_gain": str(music_gain),
    }
    return metadata


def _read_mono(path: Path) -> tuple[array[int], int]:
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise AudioError("local mixer requires 16-bit mono WAV inputs")
        samples = array("h")
        samples.frombytes(source.readframes(source.getnframes()))
        return samples, source.getframerate()


class AudioService:
    def __init__(self, services: Services) -> None:
        self.services = services

    def register(self, campaign_id: str, kind: str, metadata: AudioMetadata) -> Asset:
        workspace = self.services.storage.campaign_workspace(campaign_id)
        if not metadata.path.resolve().is_relative_to(workspace):
            raise AudioError("audio asset is outside the campaign workspace")
        asset = self.services.assets.save(
            Asset(
                campaign_id=campaign_id,
                asset_type=kind,
                status="READY",
                filepath=str(metadata.path.resolve().relative_to(workspace)),
                source=metadata.provider,
                provider=metadata.provider,
                checksum=sha256_file(metadata.path),
                provenance={
                    **metadata.provenance,
                    "duration_seconds": metadata.duration_seconds,
                    "sample_rate": metadata.sample_rate,
                    "peak": metadata.peak,
                    "rms_dbfs": metadata.rms_dbfs,
                },
            )
        )
        self.services.ledger.append(
            LedgerEvent(
                campaign_id=campaign_id,
                stage="AUDIO_PRODUCTION",
                event_type="audio_asset_created",
                status="COMPLETE",
                provider=metadata.provider,
                output_asset_ids=[asset.id],
                details={"provenance": metadata.provenance, "kind": kind},
            )
        )
        return asset
