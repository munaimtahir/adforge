"""Lenient v1 QC blockers, advisories, and targeted repair planning."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field

from adforge.models import (
    Campaign,
    CampaignState,
    CampaignTask,
    ProductTruthSnapshot,
    QCResult,
    Render,
)
from adforge.orchestrator import Orchestrator
from adforge.product_truth import ClaimValidationError, ProductTruthService
from adforge.renderer import EditSpec, FFmpegRenderer, RenderError
from adforge.services import Services


class Severity(StrEnum):
    BLOCKER = "BLOCKER"
    ADVISORY = "ADVISORY"


class QCFinding(BaseModel):
    code: str
    severity: Severity
    message: str
    asset_id: str | None = None


class QCPolicy(BaseModel):
    duration_tolerance_seconds: float = Field(default=0.25, ge=0, le=2)
    dimension_tolerance_pixels: int = Field(default=0, ge=0, le=20)
    max_targeted_repairs_per_task: int = Field(default=2, ge=0, le=10)
    require_audio_when_planned: bool = True
    require_cta: bool = True


class QCHook(Protocol):
    def inspect(
        self, campaign: Campaign, render: Render, spec: EditSpec
    ) -> list[QCFinding]: ...


class QCService:
    def __init__(
        self,
        services: Services,
        renderer: FFmpegRenderer,
        policy: QCPolicy | None = None,
        hooks: list[QCHook] | None = None,
    ) -> None:
        self.services = services
        self.renderer = renderer
        self.policy = policy or QCPolicy()
        self.hooks = hooks or []
        self.truth = ProductTruthService(services)

    def run(
        self,
        campaign: Campaign,
        render: Render,
        spec: EditSpec,
        snapshot: ProductTruthSnapshot,
        *,
        claims: list[str],
        required_asset_ids: list[str],
    ) -> QCResult:
        findings: list[QCFinding] = []
        workspace = self.services.storage.campaign_workspace(campaign.id)
        output = workspace / render.output_path
        try:
            media = self.renderer.probe(
                output,
                expect_audio=self.policy.require_audio_when_planned and bool(spec.audio_tracks),
            )
        except (RenderError, OSError, json.JSONDecodeError) as exc:
            findings.append(
                QCFinding(
                    code="INVALID_RENDER",
                    severity=Severity.BLOCKER,
                    message=str(exc),
                    asset_id=render.id,
                )
            )
            media = None
        if media:
            expected_width = spec.output_profile.width
            expected_height = spec.output_profile.height
            assert expected_width is not None and expected_height is not None
            dimension_error = max(
                abs(media.width - expected_width), abs(media.height - expected_height)
            )
            if dimension_error > self.policy.dimension_tolerance_pixels:
                findings.append(
                    QCFinding(
                        code="WRONG_DIMENSIONS",
                        severity=Severity.BLOCKER,
                        message=(
                            f"expected {expected_width}x{expected_height}, "
                            f"got {media.width}x{media.height}"
                        ),
                        asset_id=render.id,
                    )
                )
            duration_error = abs(
                media.duration_seconds - spec.output_profile.duration_seconds
            )
            if duration_error > self.policy.duration_tolerance_seconds:
                findings.append(
                    QCFinding(
                        code="WRONG_DURATION",
                        severity=Severity.BLOCKER,
                        message=f"duration differs by {duration_error:.3f}s",
                        asset_id=render.id,
                    )
                )
            if self.policy.require_audio_when_planned and spec.audio_tracks and not media.has_audio:
                findings.append(
                    QCFinding(
                        code="MISSING_AUDIO",
                        severity=Severity.BLOCKER,
                        message="planned audio stream is missing",
                        asset_id=render.id,
                    )
                )
        if self.policy.require_cta and spec.cta is None:
            findings.append(
                QCFinding(
                    code="MISSING_CTA",
                    severity=Severity.BLOCKER,
                    message="required CTA is absent",
                    asset_id=render.id,
                )
            )
        for claim in claims:
            try:
                self.truth.validate_claim(snapshot, claim)
            except ClaimValidationError as exc:
                findings.append(
                    QCFinding(
                        code="FALSE_OR_UNSUPPORTED_CLAIM",
                        severity=Severity.BLOCKER,
                        message=str(exc),
                    )
                )
        for asset_id in required_asset_ids:
            asset = self.services.assets.get(asset_id)
            missing = asset is None or asset.status != "READY"
            if asset is not None and not missing:
                path = workspace / asset.filepath
                missing = not path.is_file() or path.stat().st_size == 0
            if missing:
                findings.append(
                    QCFinding(
                        code="MISSING_REQUIRED_ASSET",
                        severity=Severity.BLOCKER,
                        message=f"required asset is unavailable: {asset_id}",
                        asset_id=asset_id,
                    )
                )
        for hook in self.hooks:
            findings.extend(hook.inspect(campaign, render, spec))
        blockers = [item.message for item in findings if item.severity == Severity.BLOCKER]
        advisories = [item.message for item in findings if item.severity == Severity.ADVISORY]
        result = self.services.qc_results.save(
            QCResult(
                campaign_id=campaign.id,
                render_id=render.id,
                passed=not blockers,
                blockers=blockers,
                advisories=advisories,
                metrics={"findings": [item.model_dump(mode="json") for item in findings]},
            )
        )
        report_path = workspace / "qc" / f"qc-{result.id}.json"
        report_path.write_text(result.model_dump_json(indent=2) + "\n")
        return result


class RepairPlan(BaseModel):
    repair_task_id: str | None = None
    targeted_asset_ids: list[str] = Field(default_factory=list)
    state: str
    reason: str


class RepairPlanner:
    def __init__(self, services: Services, policy: QCPolicy | None = None) -> None:
        self.services = services
        self.policy = policy or QCPolicy()
        self.orchestrator = Orchestrator(services)

    def plan(
        self, campaign: Campaign, failed_task: CampaignTask, result: QCResult
    ) -> RepairPlan:
        findings = [QCFinding.model_validate(item) for item in result.metrics.get("findings", [])]
        blockers = [item for item in findings if item.severity == Severity.BLOCKER]
        if not blockers:
            return RepairPlan(state="NO_REPAIR", reason="advisories do not require repair")
        previous = [
            task
            for task in self.services.tasks.find_by("campaign_id", campaign.id)
            if task.task_type == f"repair:{failed_task.task_type}"
        ]
        if len(previous) >= self.policy.max_targeted_repairs_per_task:
            current = self.services.campaigns.get(campaign.id)
            if current and current.state not in {
                CampaignState.BLOCKED,
                CampaignState.WAITING_FOR_USER,
            }:
                self.orchestrator.transition(campaign.id, CampaignState.BLOCKED)
            return RepairPlan(
                state="BUDGET_EXHAUSTED",
                reason="targeted repair budget exhausted; campaign blocked with state preserved",
            )
        targets = sorted({item.asset_id for item in blockers if item.asset_id})
        repair = self.orchestrator.create_repair_task(campaign.id, failed_task, targets)
        current = self.services.campaigns.get(campaign.id)
        if current and current.state == CampaignState.QC:
            self.orchestrator.transition(campaign.id, CampaignState.REPAIR)
        return RepairPlan(
            repair_task_id=repair.id,
            targeted_asset_ids=targets,
            state="REPAIR_SCHEDULED",
            reason="mandatory blocker requires targeted repair",
        )
