from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from adforge.creative import AssetPlanOutput, CreativeOutputError, CreativePipeline
from adforge.models import Campaign, ProductTruthSnapshot
from adforge.product_truth import ClaimValidationError
from adforge.services import Services


@pytest.fixture
def setup(tmp_path: Path) -> tuple[Services, Campaign, ProductTruthSnapshot]:
    services = Services(tmp_path / "runtime", Path("schemas"))
    services.initialize()
    campaign = services.campaigns.save(
        Campaign(product_id="product-1", name="Launch", brief="Show the approved feature")
    )
    truth = {
        "product_id": "product-1",
        "product_name": "Fixture",
        "approved_features": ["Organizes receipts"],
        "prohibited_claims": ["Guarantees reimbursement"],
        "known_limitations": [],
        "privacy_claims": [],
        "audiences": ["Homeowners"],
        "apk_locations": [],
        "demo_workflows": [{"name": "Add fictional receipt"}],
        "evidence": [
            {"claim": "Organizes receipts", "status": "CURRENT", "source": "APK"}
        ],
        "last_verified_at": "2026-08-28T00:00:00Z",
    }
    snapshot = services.truth_snapshots.save(
        ProductTruthSnapshot(
            product_id="product-1",
            campaign_id=campaign.id,
            checksum="a" * 64,
            truth=truth,
            provenance=truth["evidence"],
        )
    )
    return services, campaign, snapshot


def test_request_has_role_specific_context_and_truth_reference(
    setup: tuple[Services, Campaign, ProductTruthSnapshot],
) -> None:
    services, campaign, snapshot = setup
    request = CreativePipeline(services).build_request("script", campaign, snapshot)
    assert request.context["product_truth_snapshot_id"] == snapshot.id
    assert request.context["approved_features"] == ["Organizes receipts"]
    assert "evidence" not in request.context
    assert "apk_locations" not in request.context


def test_unsupported_claim_is_rejected(
    setup: tuple[Services, Campaign, ProductTruthSnapshot],
) -> None:
    services, campaign, snapshot = setup
    output = {
        "target_duration_seconds": 20,
        "lines": [
            {
                "start_seconds": 0,
                "end_seconds": 20,
                "text": "Everything is automatic",
                "mode": "NARRATION",
                "claim": "Automates every warranty claim",
            }
        ],
    }
    with pytest.raises(ClaimValidationError, match="unsupported"):
        CreativePipeline(services).persist("script", campaign, snapshot, output)


def test_storyboard_must_reconcile_to_target_duration(
    setup: tuple[Services, Campaign, ProductTruthSnapshot],
) -> None:
    services, campaign, snapshot = setup
    invalid = {
        "target_duration_seconds": 20,
        "scenes": [
            {
                "scene_id": "one",
                "start_seconds": 0,
                "end_seconds": 9,
                "description": "Hook",
                "framing": "close",
            },
            {
                "scene_id": "two",
                "start_seconds": 10,
                "end_seconds": 20,
                "description": "Proof",
                "framing": "phone",
            },
        ],
    }
    with pytest.raises(CreativeOutputError, match="gap or overlap"):
        CreativePipeline(services).persist("storyboard", campaign, snapshot, invalid)


def test_asset_plan_dependencies_and_deterministic_routing() -> None:
    with pytest.raises(ValidationError, match="unresolved"):
        AssetPlanOutput.model_validate(
            {
                "assets": [
                    {
                        "asset_id": "cta",
                        "scene_ids": ["end"],
                        "classification": "RENDER_GRAPHIC",
                        "description": "CTA",
                        "deterministic": True,
                        "dependencies": ["missing-logo"],
                    }
                ]
            }
        )
    with pytest.raises(ValidationError, match="deterministic asset"):
        AssetPlanOutput.model_validate(
            {
                "assets": [
                    {
                        "asset_id": "cta",
                        "scene_ids": ["end"],
                        "classification": "GENERATE_VIDEO",
                        "description": "CTA text card",
                        "deterministic": True,
                    }
                ]
            }
        )


def test_repeat_execution_versions_outputs_without_overwrite(
    setup: tuple[Services, Campaign, ProductTruthSnapshot],
) -> None:
    services, campaign, snapshot = setup
    pipeline = CreativePipeline(services)
    output = {
        "hook": "Where is the receipt?",
        "positioning": "Organize first",
        "narrative": ["Problem", "Proof", "CTA"],
        "claims": ["Organizes receipts"],
    }
    pipeline.persist("creative-strategy", campaign, snapshot, output)
    pipeline.persist("creative-strategy", campaign, snapshot, output)
    directory = services.storage.campaign_workspace(campaign.id) / "strategy"
    assert (directory / "creative-strategy.v1.json").exists()
    assert (directory / "creative-strategy.v2.json").exists()
    assert len(services.ledger.read(campaign.id)) == 2
