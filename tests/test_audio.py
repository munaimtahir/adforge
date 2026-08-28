from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from adforge.audio import (
    AudioError,
    AudioService,
    LocalProceduralMusicProvider,
    LocalProceduralVoiceProvider,
    MusicRequest,
    VoiceRequest,
    generate_local_sfx,
    mix_wav,
    validate_wav,
)
from adforge.models import Campaign
from adforge.services import Services


def test_unauthorized_voice_clone_is_blocked() -> None:
    with pytest.raises(ValidationError, match="authorization"):
        VoiceRequest(text="Narration", clone_reference=Path("voice.wav"))


def test_local_audio_assets_validate_with_timing_and_provenance(tmp_path: Path) -> None:
    voice = LocalProceduralVoiceProvider().synthesize(
        VoiceRequest(text="A short truthful narration", target_duration_seconds=2),
        tmp_path / "voice.wav",
    )
    music = LocalProceduralMusicProvider().generate(
        MusicRequest(duration_seconds=2), tmp_path / "music.wav"
    )
    sfx = generate_local_sfx(tmp_path / "sfx.wav")
    assert voice.duration_seconds == pytest.approx(2, abs=0.01)
    assert voice.provenance["clone_authorized"] == "false"
    assert "license" in music.provenance
    assert sfx.peak < 1


def test_invalid_audio_is_rejected(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.wav"
    invalid.write_bytes(b"not audio")
    with pytest.raises(AudioError, match="invalid WAV"):
        validate_wav(invalid)


def test_ducked_mix_has_no_clipping(tmp_path: Path) -> None:
    voice = LocalProceduralVoiceProvider().synthesize(
        VoiceRequest(text="Narration", target_duration_seconds=1), tmp_path / "voice.wav"
    )
    music = LocalProceduralMusicProvider().generate(
        MusicRequest(duration_seconds=1), tmp_path / "music.wav"
    )
    mixed = mix_wav(voice.path, music.path, tmp_path / "mix.wav")
    assert mixed.peak <= 0.95
    assert mixed.duration_seconds == pytest.approx(1, abs=0.01)


def test_audio_service_records_asset_and_ledger_provenance(tmp_path: Path) -> None:
    services = Services(tmp_path / "runtime", Path("schemas"))
    services.initialize()
    campaign = services.campaigns.save(
        Campaign(product_id="product", name="Audio", brief="Audio brief")
    )
    workspace = services.storage.campaign_workspace(campaign.id)
    metadata = LocalProceduralVoiceProvider().synthesize(
        VoiceRequest(text="Narration", target_duration_seconds=1),
        workspace / "audio" / "voice" / "narration.wav",
    )
    asset = AudioService(services).register(campaign.id, "narration", metadata)
    assert asset.provenance["duration_seconds"] == pytest.approx(1, abs=0.01)
    assert services.ledger.read(campaign.id)[0].details["provenance"]["type"]
