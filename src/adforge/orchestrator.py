"""Durable campaign state machine and idempotent task orchestrator."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from adforge.models import Campaign, CampaignState, CampaignTask, LedgerEvent, TaskState, utc_now
from adforge.services import Services


class TransitionError(ValueError):
    pass


class ActiveCampaignError(RuntimeError):
    pass


class TaskBlockedError(RuntimeError):
    pass


PRIMARY_STATES = (
    CampaignState.CREATED,
    CampaignState.PRODUCT_TRUTH_VALIDATION,
    CampaignState.STRATEGY,
    CampaignState.SCRIPT,
    CampaignState.STORYBOARD,
    CampaignState.ASSET_PLAN,
    CampaignState.ASSET_GENERATION,
    CampaignState.APP_CAPTURE,
    CampaignState.AUDIO_PRODUCTION,
    CampaignState.EDIT_PLAN,
    CampaignState.DRAFT_RENDER,
    CampaignState.QC,
    CampaignState.FINAL_RENDER,
    CampaignState.EXPORT,
    CampaignState.COMPLETE,
)
EXCEPTIONAL_STATES = {
    CampaignState.PAUSED,
    CampaignState.BLOCKED,
    CampaignState.FAILED,
    CampaignState.WAITING_FOR_EXTERNAL_ASSET,
    CampaignState.WAITING_FOR_USER,
    CampaignState.WAITING_FOR_WORKER,
}

LEGAL_TRANSITIONS: dict[CampaignState, set[CampaignState]] = {
    state: {PRIMARY_STATES[index + 1]}
    for index, state in enumerate(PRIMARY_STATES[:-1])
}
LEGAL_TRANSITIONS[CampaignState.QC] = {CampaignState.REPAIR, CampaignState.FINAL_RENDER}
LEGAL_TRANSITIONS[CampaignState.REPAIR] = {CampaignState.DRAFT_RENDER, CampaignState.QC}
LEGAL_TRANSITIONS[CampaignState.COMPLETE] = set()
for state in PRIMARY_STATES + (CampaignState.REPAIR,):
    if state != CampaignState.COMPLETE:
        LEGAL_TRANSITIONS[state] |= EXCEPTIONAL_STATES
LEGAL_TRANSITIONS[CampaignState.WAITING_FOR_WORKER] = {CampaignState.BLOCKED}


TaskAction = Callable[[int], dict[str, Any]]


class Orchestrator:
    def __init__(self, services: Services) -> None:
        self.services = services

    def transition(self, campaign_id: str, target: CampaignState) -> Campaign:
        campaign = self._campaign(campaign_id)
        if target not in LEGAL_TRANSITIONS.get(campaign.state, set()):
            raise TransitionError(f"illegal transition: {campaign.state} -> {target}")
        if target not in EXCEPTIONAL_STATES | {CampaignState.COMPLETE} and not campaign.active:
            self.acquire_lease(campaign_id)
            campaign = self._campaign(campaign_id)
        if target not in EXCEPTIONAL_STATES:
            resume_state = None
        elif campaign.state in EXCEPTIONAL_STATES:
            # Chaining between exceptional states (e.g. a WorkerJob exhausting its
            # attempts moves WAITING_FOR_WORKER -> BLOCKED) must not overwrite the
            # real resume point with the exceptional state we're leaving -- found
            # live: that overwrite permanently lost resume_state=ASSET_GENERATION,
            # replacing it with resume_state=WAITING_FOR_WORKER, so resuming just
            # bounced straight back into WAITING_FOR_WORKER with nothing to wait
            # for and no way to ever continue the campaign again.
            resume_state = campaign.resume_state
        else:
            resume_state = campaign.state
        updated = campaign.model_copy(
            update={
                "state": target,
                "resume_state": resume_state,
                "active": target not in EXCEPTIONAL_STATES | {CampaignState.COMPLETE},
            }
        )
        saved = self.services.campaigns.save(updated)
        self._ledger(
            saved,
            "campaign_transition",
            "COMPLETE",
            {"from": campaign.state, "to": target},
        )
        return saved

    def resume(self, campaign_id: str) -> Campaign:
        campaign = self._campaign(campaign_id)
        if campaign.state not in EXCEPTIONAL_STATES or campaign.resume_state is None:
            raise TransitionError("campaign is not resumable")
        self.acquire_lease(campaign_id)
        target = campaign.resume_state
        updated = campaign.model_copy(
            update={"state": target, "resume_state": None, "active": True}
        )
        saved = self.services.campaigns.save(updated)
        self._ledger(saved, "campaign_resumed", "COMPLETE", {"to": target})
        return saved

    def pause(self, campaign_id: str) -> Campaign:
        return self.transition(campaign_id, CampaignState.PAUSED)

    def acquire_lease(self, campaign_id: str) -> Campaign:
        with self.services.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute("SELECT id, payload_json FROM campaigns").fetchall()
            active = [
                row["id"]
                for row in rows
                if json.loads(row["payload_json"]).get("active") and row["id"] != campaign_id
            ]
            if active:
                raise ActiveCampaignError(f"campaign {active[0]} already holds the active lease")
            row = next((item for item in rows if item["id"] == campaign_id), None)
            if row is None:
                raise KeyError(f"campaign not found: {campaign_id}")
            campaign = Campaign.model_validate_json(row["payload_json"])
            saved = campaign.model_copy(update={"active": True, "updated_at": utc_now()})
            connection.execute(
                "UPDATE campaigns SET payload_json=?, updated_at=? WHERE id=?",
                (saved.model_dump_json(), saved.updated_at.isoformat(), saved.id),
            )
        self._ledger(saved, "active_lease_acquired", "COMPLETE")
        return saved

    def release_lease(self, campaign_id: str) -> Campaign:
        campaign = self._campaign(campaign_id)
        saved = self.services.campaigns.save(campaign.model_copy(update={"active": False}))
        self._ledger(saved, "active_lease_released", "COMPLETE")
        return saved

    def create_task(
        self,
        campaign_id: str,
        task_type: str,
        idempotency_key: str,
        *,
        dependencies: list[str] | None = None,
        targeted_asset_ids: list[str] | None = None,
    ) -> CampaignTask:
        existing = self.services.tasks.find_by("idempotency_key", idempotency_key)
        matching = [task for task in existing if task.campaign_id == campaign_id]
        if matching:
            return matching[0]
        return self.services.tasks.save(
            CampaignTask(
                campaign_id=campaign_id,
                task_type=task_type,
                idempotency_key=idempotency_key,
                dependencies=dependencies or [],
                targeted_asset_ids=targeted_asset_ids or [],
            )
        )

    def execute_task(self, task_id: str, action: TaskAction) -> CampaignTask:
        task = self._task(task_id)
        if task.state == TaskState.COMPLETE:
            return task
        incomplete = [
            dependency
            for dependency in task.dependencies
            if (candidate := self.services.tasks.get(dependency)) is None
            or candidate.state != TaskState.COMPLETE
        ]
        if incomplete:
            raise TaskBlockedError(f"incomplete task dependencies: {', '.join(incomplete)}")
        last_error = task.failure_summary
        while task.attempt < task.max_attempts:
            attempt = task.attempt + 1
            task = self.services.tasks.save(
                task.model_copy(update={"state": TaskState.RUNNING, "attempt": attempt})
            )
            self._task_ledger(task, "task_attempt_started", "RUNNING")
            try:
                output = action(attempt)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                task = self.services.tasks.save(
                    task.model_copy(
                        update={"state": TaskState.FAILED, "failure_summary": last_error}
                    )
                )
                self._task_ledger(
                    task, "task_attempt_failed", "FAILED", {"failure_summary": last_error}
                )
                continue
            completed = self.services.tasks.save(
                task.model_copy(
                    update={
                        "state": TaskState.COMPLETE,
                        "output": output,
                        "failure_summary": None,
                    }
                )
            )
            self._task_ledger(completed, "task_completed", "COMPLETE")
            return completed
        self.services.tasks.save(
            task.model_copy(update={"state": TaskState.BLOCKED, "failure_summary": last_error})
        )
        campaign = self._campaign(task.campaign_id)
        if campaign.state not in EXCEPTIONAL_STATES:
            self.transition(campaign.id, CampaignState.BLOCKED)
        raise TaskBlockedError(last_error or "task exhausted its attempt budget")

    def recover(self) -> list[Campaign]:
        for task in self.services.tasks.find_by("state", TaskState.RUNNING):
            recovered_state = (
                TaskState.BLOCKED if task.attempt >= task.max_attempts else TaskState.PENDING
            )
            self.services.tasks.save(task.model_copy(update={"state": recovered_state}))
        return [campaign for campaign in self.services.campaigns.list() if campaign.active]

    def create_repair_task(
        self, campaign_id: str, failed_task: CampaignTask, asset_ids: list[str]
    ) -> CampaignTask:
        version = len(
            [
                task
                for task in self.services.tasks.find_by("campaign_id", campaign_id)
                if task.task_type == f"repair:{failed_task.task_type}"
            ]
        ) + 1
        return self.create_task(
            campaign_id,
            f"repair:{failed_task.task_type}",
            f"repair:{failed_task.id}:v{version}",
            targeted_asset_ids=asset_ids,
        )

    def _campaign(self, campaign_id: str) -> Campaign:
        campaign = self.services.campaigns.get(campaign_id)
        if campaign is None:
            raise KeyError(f"campaign not found: {campaign_id}")
        return campaign

    def _task(self, task_id: str) -> CampaignTask:
        task = self.services.tasks.get(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        return task

    def _ledger(
        self,
        campaign: Campaign,
        event_type: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.services.ledger.append(
            LedgerEvent(
                campaign_id=campaign.id,
                stage=campaign.state,
                event_type=event_type,
                status=status,
                details=details or {},
            )
        )

    def _task_ledger(
        self,
        task: CampaignTask,
        event_type: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        campaign = self._campaign(task.campaign_id)
        self.services.ledger.append(
            LedgerEvent(
                campaign_id=task.campaign_id,
                stage=campaign.state,
                task_id=task.id,
                event_type=event_type,
                attempt=task.attempt,
                status=status,
                details=details or {},
            )
        )
