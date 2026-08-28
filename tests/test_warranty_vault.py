from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from adforge.auth import hash_password
from adforge.bootstrap import ensure_warranty_vault_product
from adforge.models import Campaign, CampaignState, TruthReadiness
from adforge.services import Services
from adforge.web import WebContext, create_app


def test_warranty_vault_seed_is_unknown_idempotent_and_claim_free(tmp_path: Path) -> None:
    services = Services(tmp_path / "runtime", Path("schemas"))
    services.initialize()
    first = ensure_warranty_vault_product(services)
    second = ensure_warranty_vault_product(services)
    assert first == second
    assert first.truth_readiness == TruthReadiness.UNKNOWN
    record = json.loads(Path("products/warranty-vault/product-record.json").read_text())
    assert "approved_features" not in record
    assert "description" not in record


def test_acceptance_campaign_cannot_start_without_ready_truth(tmp_path: Path) -> None:
    app = create_app(
        runtime_root=tmp_path / "runtime",
        schema_root=Path("schemas"),
        secret_key="fixture-secret-key-that-is-long-enough-123",  # noqa: S106
        password_hash=hash_password(
            "fixture-password-123", salt=b"0123456789abcdef"  # noqa: S106
        ),
        import_root=tmp_path / "imports",
        secure_cookie=False,
    )
    context: WebContext = app.state.context
    campaign = context.services.campaigns.save(
        Campaign(
            product_id="warranty-vault",
            name="Warranty Vault acceptance",
            brief="Locked acceptance brief, pending truth",
        )
    )
    client = TestClient(app)
    client.post("/login", data={"password": "fixture-password-123"})
    page = client.get(f"/campaigns/{campaign.id}")
    csrf_match = re.search(r'name="csrf" value="([^"]+)"', page.text)
    assert csrf_match is not None
    response = client.post(
        f"/campaigns/{campaign.id}/start",
        data={"csrf": csrf_match.group(1)},
    )
    assert response.status_code == 409
    persisted = context.services.campaigns.get(campaign.id)
    assert persisted is not None
    assert persisted.state == CampaignState.CREATED
    assert persisted.active is False


def test_handoff_and_readiness_documents_name_every_missing_input() -> None:
    handoff = Path("docs/10-acceptance/WARRANTY_VAULT_HANDOFF_REQUEST.md").read_text()
    readiness = Path("docs/10-acceptance/WARRANTY_VAULT_READINESS.md").read_text()
    for required in (
        "PRODUCT_TRUTH.json",
        "APP_CAPTURE_WORKFLOWS.md",
        "CLAIM_EVIDENCE.md",
        "SHA-256",
        "package ID",
        "brand",
        "prohibited",
    ):
        assert required in handoff
    assert "NOT READY" in readiness
    assert "not claim evidence" in readiness
