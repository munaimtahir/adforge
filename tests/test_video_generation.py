from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from adforge.models import Campaign, CampaignState
from adforge.services import Services
from adforge.storage import sha256_file
from adforge.video_generation import (
    FlowBrowserVideoProvider,
    FlowLoginRequired,
    GenerationHandoffService,
    GenerationScene,
    ReturnManifestError,
    VideoGenerationHealth,
    VideoGenerationRequest,
)


class FakeFlowDriver:
    def __init__(self, failures: int = 0, login_required: bool = False) -> None:
        self.failures = failures
        self.login_required = login_required
        self.calls = 0

    def health(self) -> VideoGenerationHealth:
        return VideoGenerationHealth(
            available=not self.login_required,
            login_state="LOGIN_REQUIRED" if self.login_required else "AUTHENTICATED",
        )

    def generate(
        self, scene: GenerationScene, destination: Path, preferred_model: str, mode: str
    ) -> Path:
        self.calls += 1
        if self.login_required:
            raise FlowLoginRequired("Authenticate persistent profile")
        if self.calls <= self.failures:
            raise RuntimeError("temporary provider failure")
        destination.write_bytes(b"fixture video bytes")
        return destination


def generation_request(campaign_id: str, reference: str = "") -> VideoGenerationRequest:
    return VideoGenerationRequest(
        campaign_id=campaign_id,
        credit_budget=3,
        scenes=[
            GenerationScene(
                scene_id="scene-1",
                prompt="A receipt beside an appliance",
                negative_constraints=["no critical generated text"],
                reference_paths=[reference] if reference else [],
                aspect_ratio="9:16",
                duration_seconds=6,
                expected_filename="scene-1.mp4",
            )
        ],
    )


def test_flow_contract_retries_and_login_is_actionable(tmp_path: Path) -> None:
    driver = FakeFlowDriver(failures=2)
    output = FlowBrowserVideoProvider(driver, tmp_path).generate(generation_request("campaign"))
    assert output[0].attempts == 3
    assert driver.calls == 3
    blocked = FlowBrowserVideoProvider(FakeFlowDriver(login_required=True), tmp_path / "b")
    with pytest.raises(FlowLoginRequired, match="Authenticate"):
        blocked.generate(generation_request("campaign"))


def test_declared_attempts_must_fit_budget() -> None:
    with pytest.raises(ValidationError, match="budget"):
        VideoGenerationRequest(
            campaign_id="campaign",
            credit_budget=2,
            scenes=[
                GenerationScene(
                    scene_id="scene",
                    prompt="prompt",
                    aspect_ratio="9:16",
                    duration_seconds=6,
                    expected_filename="scene.mp4",
                    max_attempts=3,
                )
            ],
        )


def test_generation_handoff_round_trip_and_resume(tmp_path: Path) -> None:
    services = Services(tmp_path / "runtime", Path("schemas"))
    services.initialize()
    campaign = services.campaigns.save(
        Campaign(
            product_id="product",
            name="Campaign",
            brief="Brief",
            state=CampaignState.WAITING_FOR_EXTERNAL_ASSET,
            resume_state=CampaignState.ASSET_GENERATION,
        )
    )
    workspace = services.storage.campaign_workspace(campaign.id)
    reference = workspace / "storyboard" / "reference.png"
    reference.write_bytes(b"reference")
    request = generation_request(campaign.id, "storyboard/reference.png")
    handoff = GenerationHandoffService(services)
    package = handoff.export(request)
    root = workspace / package.request_path
    exported = json.loads((root / "GENERATION_REQUEST.json").read_text())
    assert exported["scenes"][0]["packaged_references"][0]["checksum"] == sha256_file(
        reference
    )
    returned = workspace / package.return_path / "scene-1.mp4"
    returned.write_bytes(b"representative returned fixture")
    (returned.parent / "GENERATION_RETURN_MANIFEST.json").write_text(
        json.dumps(
            {
                "files": [
                    {
                        "filename": "scene-1.mp4",
                        "checksum": sha256_file(returned),
                        "provider": "fixture-external",
                        "model": "fixture",
                        "attempts": 1,
                    }
                ]
            }
        )
    )
    assets = handoff.import_return(package.id, request)
    assert assets[0].checksum == sha256_file(returned)
    resumed = services.campaigns.get(campaign.id)
    assert resumed is not None and resumed.state == CampaignState.ASSET_GENERATION


def test_generation_handoff_rejects_wrong_or_missing_files(tmp_path: Path) -> None:
    services = Services(tmp_path / "runtime", Path("schemas"))
    services.initialize()
    campaign = services.campaigns.save(
        Campaign(product_id="product", name="Campaign", brief="Brief")
    )
    request = generation_request(campaign.id)
    handoff = GenerationHandoffService(services)
    package = handoff.export(request)
    return_root = services.storage.campaign_workspace(campaign.id) / package.return_path
    (return_root / "wrong.mp4").write_bytes(b"wrong")
    (return_root / "GENERATION_RETURN_MANIFEST.json").write_text(
        json.dumps(
            {"files": [{"filename": "wrong.mp4", "checksum": "a" * 64}]}
        )
    )
    with pytest.raises(ReturnManifestError, match="filenames"):
        handoff.import_return(package.id, request)
