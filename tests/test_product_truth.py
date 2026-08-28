from __future__ import annotations

import json
from pathlib import Path

import pytest

from adforge.models import Campaign, Product, TruthReadiness
from adforge.product_truth import ClaimValidationError, ProductTruthError, ProductTruthService
from adforge.services import Services


@pytest.fixture
def services(tmp_path: Path) -> Services:
    value = Services(tmp_path / "runtime", Path("schemas"))
    value.initialize()
    return value


@pytest.fixture
def truth() -> dict[str, object]:
    return {
        "product_id": "product-1",
        "product_name": "Fixture App",
        "package_id": "app.fixture",
        "current_version": "1.0",
        "description": "Fixture description",
        "approved_features": ["Organizes receipts"],
        "prohibited_claims": ["Guarantees reimbursement"],
        "known_limitations": ["No reimbursement service"],
        "privacy_claims": [],
        "audiences": ["Homeowners"],
        "apk_locations": ["/safe/app.apk"],
        "demo_workflows": [{"name": "Add fictional receipt"}],
        "evidence": [
            {"claim": "Organizes receipts", "status": "CURRENT", "source": "APK test"},
            {"claim": "Cloud sync", "status": "UNKNOWN", "source": "Not verified"},
        ],
        "last_verified_at": "2026-08-28T00:00:00Z",
    }


def test_invalid_truth_and_missing_evidence_are_rejected(
    services: Services, truth: dict[str, object]
) -> None:
    service = ProductTruthService(services)
    invalid = dict(truth)
    invalid.pop("product_name")
    with pytest.raises(ProductTruthError, match="schema validation"):
        service.validate(invalid)
    no_evidence = dict(truth, evidence=[])
    with pytest.raises(ProductTruthError, match="evidence"):
        service.validate(no_evidence)
    stale_evidence = dict(
        truth,
        evidence=[{"claim": "Organizes receipts", "status": "UNKNOWN", "source": "old"}],
    )
    with pytest.raises(ProductTruthError, match="lack CURRENT evidence"):
        service.validate(stale_evidence)


def test_json_and_markdown_import(
    services: Services, truth: dict[str, object], tmp_path: Path
) -> None:
    service = ProductTruthService(services)
    json_path = tmp_path / "truth.json"
    json_path.write_text(json.dumps(truth))
    assert service.parse(json_path) == truth
    markdown_path = tmp_path / "truth.md"
    markdown_path.write_text("# Handoff\n\n```json\n" + json.dumps(truth) + "\n```\n")
    assert service.parse(markdown_path) == truth


def test_snapshot_is_immutable_and_provenance_is_ledgered(
    services: Services, truth: dict[str, object]
) -> None:
    service = ProductTruthService(services)
    product = services.products.save(
        Product(
            id="product-1",
            name="Fixture App",
            slug="fixture",
            truth_readiness=TruthReadiness.READY,
        )
    )
    campaign = services.campaigns.save(
        Campaign(product_id=product.id, name="Campaign", brief="Truthful brief")
    )
    snapshot = service.snapshot_for_campaign(product, campaign, truth)
    changed = snapshot.model_copy(update={"truth": dict(snapshot.truth, description="changed")})
    with pytest.raises(ValueError, match="immutable"):
        services.truth_snapshots.save(changed)
    ledger = services.ledger.read(campaign.id)
    assert ledger[0].details["provenance"] == truth["evidence"]
    repeated = service.snapshot_for_campaign(product, campaign, dict(truth, description="new"))
    assert repeated == snapshot


def test_claim_validation_enforces_approved_unknown_and_prohibited(
    services: Services, truth: dict[str, object]
) -> None:
    service = ProductTruthService(services)
    product = services.products.save(
        Product(
            id="product-1",
            name="Fixture App",
            slug="fixture",
            truth_readiness=TruthReadiness.READY,
        )
    )
    campaign = services.campaigns.save(
        Campaign(product_id=product.id, name="Campaign", brief="Truthful brief")
    )
    snapshot = service.snapshot_for_campaign(product, campaign, truth)
    service.validate_claim(snapshot, "Organizes receipts")
    with pytest.raises(ClaimValidationError, match="unknown"):
        service.validate_claim(snapshot, "Cloud sync")
    with pytest.raises(ClaimValidationError, match="prohibited"):
        service.validate_claim(snapshot, "Guarantees reimbursement")
    with pytest.raises(ClaimValidationError, match="unsupported"):
        service.validate_claim(snapshot, "Automates every warranty claim")


def test_product_import_sets_ready_only_after_validation(
    services: Services, truth: dict[str, object], tmp_path: Path
) -> None:
    product = services.products.save(Product(id="product-1", name="Fixture", slug="fixture"))
    path = tmp_path / "truth.json"
    path.write_text(json.dumps(truth))
    updated = ProductTruthService(services).import_for_product(product, path)
    assert updated.truth_readiness == TruthReadiness.READY
