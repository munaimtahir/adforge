"""Stage worker that autonomously advances durable campaigns until terminal or waiting."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

from pydantic import BaseModel, Field

from adforge.models import Campaign, CampaignState, CampaignTask, TaskState, TruthReadiness
from adforge.orchestrator import Orchestrator, TaskBlockedError
from adforge.services import Services

StageHandler = Callable[[Campaign, CampaignTask, int], dict[str, Any]]


DEFAULT_NEXT: dict[CampaignState, CampaignState] = {
    CampaignState.PRODUCT_TRUTH_VALIDATION: CampaignState.STRATEGY,
    CampaignState.STRATEGY: CampaignState.SCRIPT,
    CampaignState.SCRIPT: CampaignState.STORYBOARD,
    CampaignState.STORYBOARD: CampaignState.ASSET_PLAN,
    CampaignState.ASSET_PLAN: CampaignState.ASSET_GENERATION,
    CampaignState.ASSET_GENERATION: CampaignState.APP_CAPTURE,
    CampaignState.APP_CAPTURE: CampaignState.AUDIO_PRODUCTION,
    CampaignState.AUDIO_PRODUCTION: CampaignState.EDIT_PLAN,
    CampaignState.EDIT_PLAN: CampaignState.DRAFT_RENDER,
    CampaignState.DRAFT_RENDER: CampaignState.QC,
    CampaignState.QC: CampaignState.FINAL_RENDER,
    CampaignState.REPAIR: CampaignState.QC,
    CampaignState.FINAL_RENDER: CampaignState.EXPORT,
    CampaignState.EXPORT: CampaignState.COMPLETE,
}

STOPPED_STATES = {
    CampaignState.PAUSED,
    CampaignState.BLOCKED,
    CampaignState.FAILED,
    CampaignState.WAITING_FOR_EXTERNAL_ASSET,
    CampaignState.WAITING_FOR_USER,
    CampaignState.COMPLETE,
}


class WorkerResult(BaseModel):
    campaign_id: str
    state: CampaignState
    stages_executed: list[CampaignState] = Field(default_factory=list)
    reason: str


class CampaignWorker:
    def __init__(self, services: Services, handlers: dict[CampaignState, StageHandler]) -> None:
        self.services = services
        self.handlers = handlers
        self.orchestrator = Orchestrator(services)

    def run(self, campaign_id: str, *, max_stages: int | None = None) -> WorkerResult:
        campaign = self._campaign(campaign_id)
        if campaign.state == CampaignState.CREATED:
            product = self.services.products.get(campaign.product_id)
            if product is None or product.truth_readiness != TruthReadiness.READY:
                return WorkerResult(
                    campaign_id=campaign.id,
                    state=campaign.state,
                    reason="Product Truth is not READY",
                )
            campaign = self.orchestrator.transition(
                campaign.id, CampaignState.PRODUCT_TRUTH_VALIDATION
            )
        if campaign.state in STOPPED_STATES:
            return WorkerResult(
                campaign_id=campaign.id,
                state=campaign.state,
                reason="campaign is terminal, paused, blocked, or waiting",
            )
        executed: list[CampaignState] = []
        while campaign.state not in STOPPED_STATES:
            if max_stages is not None and len(executed) >= max_stages:
                return WorkerResult(
                    campaign_id=campaign.id,
                    state=campaign.state,
                    stages_executed=executed,
                    reason="controlled worker stop",
                )
            stage = campaign.state
            handler = self.handlers.get(stage)
            if handler is None:
                campaign = self.orchestrator.transition(campaign.id, CampaignState.BLOCKED)
                return WorkerResult(
                    campaign_id=campaign.id,
                    state=campaign.state,
                    stages_executed=executed,
                    reason=f"no handler registered for {stage}",
                )
            version = self._stage_version(campaign.id, stage)
            task = self.orchestrator.create_task(
                campaign.id,
                stage.value.lower(),
                f"stage:{stage.value.lower()}:v{version}",
            )
            was_complete = task.state == TaskState.COMPLETE
            try:
                task = self.orchestrator.execute_task(
                    task.id,
                    partial(handler, campaign, task),
                )
            except TaskBlockedError as exc:
                blocked = self._campaign(campaign.id)
                return WorkerResult(
                    campaign_id=campaign.id,
                    state=blocked.state,
                    stages_executed=executed,
                    reason=str(exc),
                )
            executed.append(stage)
            output = task.output or {}
            waiting = output.get("waiting_state")
            if waiting and not was_complete:
                waiting_state = CampaignState(str(waiting))
                campaign = self.orchestrator.transition(campaign.id, waiting_state)
                return WorkerResult(
                    campaign_id=campaign.id,
                    state=campaign.state,
                    stages_executed=executed,
                    reason=str(output.get("reason", "waiting for external input")),
                )
            requested_next = output.get("next_state")
            target = (
                CampaignState(str(requested_next))
                if requested_next
                else DEFAULT_NEXT[stage]
            )
            campaign = self.orchestrator.transition(campaign.id, target)
        return WorkerResult(
            campaign_id=campaign.id,
            state=campaign.state,
            stages_executed=executed,
            reason="campaign reached a terminal or waiting state",
        )

    def _campaign(self, campaign_id: str) -> Campaign:
        campaign = self.services.campaigns.get(campaign_id)
        if campaign is None:
            raise KeyError(f"campaign not found: {campaign_id}")
        return campaign

    def _stage_version(self, campaign_id: str, stage: CampaignState) -> int:
        visits = sum(
            1
            for event in self.services.ledger.read(campaign_id)
            if event.event_type == "campaign_transition"
            and event.details.get("to") == stage
        )
        return max(1, visits)
