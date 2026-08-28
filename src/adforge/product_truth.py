"""Product Truth import, readiness, immutable snapshot, and claim validation."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from adforge.models import (
    Campaign,
    LedgerEvent,
    Product,
    ProductTruthSnapshot,
    TruthReadiness,
)
from adforge.services import Services


class ProductTruthError(ValueError):
    pass


class ClaimValidationError(ProductTruthError):
    pass


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def truth_checksum(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _extract_markdown_payload(text: str) -> dict[str, Any]:
    if text.startswith("---\n"):
        closing = text.find("\n---", 4)
        if closing < 0:
            raise ProductTruthError("Markdown front matter is not closed")
        parsed = yaml.safe_load(text[4:closing])
    else:
        match = re.search(r"```(?:json|yaml)\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if match is None:
            raise ProductTruthError("Markdown must contain YAML front matter or a JSON/YAML fence")
        payload = match.group(1)
        if match.group(0).lstrip().startswith("```json"):
            parsed = json.loads(payload)
        else:
            parsed = yaml.safe_load(payload)
    if not isinstance(parsed, dict):
        raise ProductTruthError("Product Truth payload must be an object")
    return parsed


class ProductTruthService:
    def __init__(self, services: Services) -> None:
        self.services = services
        schema_path = services.storage.schema_root / "product_truth.schema.json"
        self.schema: dict[str, Any] = json.loads(schema_path.read_text())

    def parse(self, path: Path) -> dict[str, Any]:
        if path.suffix.lower() == ".json":
            value = json.loads(path.read_text())
            if not isinstance(value, dict):
                raise ProductTruthError("Product Truth JSON must be an object")
            return value
        if path.suffix.lower() in {".md", ".markdown"}:
            return _extract_markdown_payload(path.read_text())
        raise ProductTruthError("Product Truth must be JSON or Markdown")

    def validate(self, truth: dict[str, Any]) -> dict[str, Any]:
        try:
            jsonschema.Draft202012Validator(self.schema).validate(truth)
        except jsonschema.ValidationError as exc:
            raise ProductTruthError(f"schema validation failed: {exc.message}") from exc
        evidence = truth.get("evidence", [])
        if not evidence:
            raise ProductTruthError("at least one evidence record is required")
        current_claims: set[str] = set()
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                raise ProductTruthError(f"evidence[{index}] must be an object")
            missing = {"claim", "status", "source"} - set(item)
            if missing:
                raise ProductTruthError(f"evidence[{index}] missing: {', '.join(sorted(missing))}")
            if item["status"] not in {"CURRENT", "UNKNOWN"}:
                raise ProductTruthError(f"evidence[{index}] status must be CURRENT or UNKNOWN")
            if item["status"] == "CURRENT":
                current_claims.add(str(item["claim"]).casefold().strip())
        unsupported = [
            claim
            for claim in truth["approved_features"]
            if claim.casefold().strip() not in current_claims
        ]
        if unsupported:
            raise ProductTruthError(
                "approved features lack CURRENT evidence: " + ", ".join(unsupported)
            )
        return truth

    def import_for_product(self, product: Product, path: Path) -> Product:
        truth = self.validate(self.parse(path))
        if truth["product_id"] != product.id:
            raise ProductTruthError("Product Truth product_id does not match product record")
        product.truth_readiness = TruthReadiness.READY
        product.truth_source_path = str(path.resolve())
        return self.services.products.save(product)

    def snapshot_for_campaign(
        self, product: Product, campaign: Campaign, truth: dict[str, Any]
    ) -> ProductTruthSnapshot:
        self.validate(truth)
        if product.truth_readiness != TruthReadiness.READY:
            raise ProductTruthError("product is not READY")
        existing = self.services.truth_snapshots.find_by("campaign_id", campaign.id)
        if existing:
            return existing[0]
        snapshot = ProductTruthSnapshot(
            product_id=product.id,
            campaign_id=campaign.id,
            checksum=truth_checksum(truth),
            truth=json.loads(canonical_json(truth)),
            provenance=list(truth["evidence"]),
        )
        saved = self.services.truth_snapshots.save(snapshot)
        workspace = self.services.storage.campaign_workspace(campaign.id)
        snapshot_path = workspace / "truth" / "product-truth.snapshot.json"
        snapshot_path.write_text(json.dumps(saved.truth, indent=2, sort_keys=True) + "\n")
        campaign.truth_snapshot_id = saved.id
        self.services.campaigns.save(campaign)
        self.services.ledger.append(
            LedgerEvent(
                campaign_id=campaign.id,
                stage="PRODUCT_TRUTH_VALIDATION",
                event_type="product_truth_snapshotted",
                status="COMPLETE",
                details={
                    "snapshot_id": saved.id,
                    "checksum": saved.checksum,
                    "provenance": saved.provenance,
                },
            )
        )
        return saved

    @staticmethod
    def validate_claim(snapshot: ProductTruthSnapshot, claim: str) -> None:
        normalized = claim.casefold().strip()
        prohibited = {item.casefold().strip() for item in snapshot.truth["prohibited_claims"]}
        if any(item in normalized or normalized in item for item in prohibited):
            raise ClaimValidationError(f"prohibited claim: {claim}")
        current = {
            str(item["claim"]).casefold().strip()
            for item in snapshot.provenance
            if item.get("status") == "CURRENT"
        }
        unknown = {
            str(item["claim"]).casefold().strip()
            for item in snapshot.provenance
            if item.get("status") == "UNKNOWN"
        }
        if normalized in unknown:
            raise ClaimValidationError(f"unknown claim: {claim}")
        if normalized not in current:
            raise ClaimValidationError(f"unsupported claim: {claim}")
