from __future__ import annotations

from pathlib import Path

import pytest

from adforge.models import Asset, Campaign, CampaignState, TaskState
from adforge.orchestrator import (
    ActiveCampaignError,
    Orchestrator,
    TaskBlockedError,
    TransitionError,
)
from adforge.services import Services


@pytest.fixture
def services(tmp_path: Path) -> Services:
    value = Services(tmp_path / "runtime", Path("schemas"))
    value.initialize()
    return value


def campaign(services: Services, name: str = "Campaign") -> Campaign:
    return services.campaigns.save(
        Campaign(product_id="product-1", name=name, brief="A truthful campaign")
    )


def test_legal_and_illegal_transitions_are_enforced(services: Services) -> None:
    created = campaign(services)
    orchestrator = Orchestrator(services)
    validating = orchestrator.transition(created.id, CampaignState.PRODUCT_TRUTH_VALIDATION)
    assert validating.state == CampaignState.PRODUCT_TRUTH_VALIDATION
    assert validating.active is True
    with pytest.raises(TransitionError, match="illegal transition"):
        orchestrator.transition(created.id, CampaignState.STORYBOARD)


def test_pause_resume_and_one_active_campaign_lease(services: Services) -> None:
    first = campaign(services, "First")
    second = campaign(services, "Second")
    orchestrator = Orchestrator(services)
    orchestrator.acquire_lease(first.id)
    with pytest.raises(ActiveCampaignError):
        orchestrator.acquire_lease(second.id)
    paused = orchestrator.pause(first.id)
    assert paused.state == CampaignState.PAUSED
    assert paused.resume_state == CampaignState.CREATED
    resumed = orchestrator.resume(first.id)
    assert resumed.state == CampaignState.CREATED
    assert resumed.active is True


def test_chained_exceptional_transitions_preserve_the_original_resume_point(
    services: Services,
) -> None:
    """The real bug found live: a WorkerJob exhausting its automated attempt
    budget moves the campaign WAITING_FOR_WORKER -> BLOCKED. Before this fix,
    transition() always captured the state being *left* as resume_state, so
    that second hop overwrote resume_state=ASSET_GENERATION (the real point to
    continue from) with resume_state=WAITING_FOR_WORKER (the exceptional state
    itself) -- permanently losing it, since resuming from BLOCKED would then
    just land back in WAITING_FOR_WORKER with nothing to wait for and no way to
    ever continue the campaign again.
    """
    created = campaign(services)
    orchestrator = Orchestrator(services)
    orchestrator.transition(created.id, CampaignState.PRODUCT_TRUTH_VALIDATION)
    orchestrator.transition(created.id, CampaignState.STRATEGY)
    orchestrator.transition(created.id, CampaignState.SCRIPT)
    orchestrator.transition(created.id, CampaignState.STORYBOARD)
    orchestrator.transition(created.id, CampaignState.ASSET_PLAN)
    orchestrator.transition(created.id, CampaignState.ASSET_GENERATION)
    waiting = orchestrator.transition(created.id, CampaignState.WAITING_FOR_WORKER)
    assert waiting.resume_state == CampaignState.ASSET_GENERATION

    blocked = orchestrator.transition(created.id, CampaignState.BLOCKED)
    assert blocked.resume_state == CampaignState.ASSET_GENERATION

    resumed = orchestrator.resume(created.id)
    assert resumed.state == CampaignState.ASSET_GENERATION
    assert resumed.active is True


def test_task_retries_initial_plus_two_and_preserves_assets(services: Services) -> None:
    created = campaign(services)
    existing_asset = services.assets.save(
        Asset(
            campaign_id=created.id,
            asset_type="video",
            status="READY",
            filepath="generated/video/good.mp4",
            checksum="a" * 64,
        )
    )
    orchestrator = Orchestrator(services)
    task = orchestrator.create_task(created.id, "generation", "generation-v1")
    attempts: list[int] = []

    def fail(attempt: int) -> dict[str, object]:
        attempts.append(attempt)
        raise RuntimeError("provider unavailable")

    with pytest.raises(TaskBlockedError, match="provider unavailable"):
        orchestrator.execute_task(task.id, fail)
    persisted = services.tasks.get(task.id)
    assert persisted is not None
    assert attempts == [1, 2, 3]
    assert persisted.attempt == 3
    assert persisted.state == TaskState.BLOCKED
    assert services.assets.get(existing_asset.id) == existing_asset


def test_completed_task_is_idempotent_and_dependencies_are_checked(services: Services) -> None:
    created = campaign(services)
    orchestrator = Orchestrator(services)
    dependency = orchestrator.create_task(created.id, "strategy", "strategy-v1")
    child = orchestrator.create_task(
        created.id, "script", "script-v1", dependencies=[dependency.id]
    )
    with pytest.raises(TaskBlockedError, match="dependencies"):
        orchestrator.execute_task(child.id, lambda _: {"script": "text"})
    calls = 0

    def complete(_: int) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"strategy": "value"}

    first = orchestrator.execute_task(dependency.id, complete)
    repeated = orchestrator.execute_task(dependency.id, complete)
    assert first == repeated
    assert calls == 1
    completed_child = orchestrator.execute_task(child.id, lambda _: {"script": "text"})
    assert completed_child.state == TaskState.COMPLETE


def test_restart_recovery_resumes_without_regenerating_completed_work(
    services: Services,
) -> None:
    created = campaign(services)
    first = Orchestrator(services)
    first.acquire_lease(created.id)
    complete = first.create_task(created.id, "strategy", "strategy-v1")
    first.execute_task(complete.id, lambda _: {"version": 1})
    interrupted = first.create_task(created.id, "script", "script-v1")
    services.tasks.save(
        interrupted.model_copy(update={"state": TaskState.RUNNING, "attempt": 1})
    )
    restarted_services = Services(services.storage.root, Path("schemas"))
    restarted_services.initialize()
    restarted = Orchestrator(restarted_services)
    active = restarted.recover()
    assert [item.id for item in active] == [created.id]
    recovered_task = restarted_services.tasks.get(interrupted.id)
    assert recovered_task is not None and recovered_task.state == TaskState.PENDING
    completed = restarted_services.tasks.get(complete.id)
    assert completed is not None and completed.state == TaskState.COMPLETE


def test_targeted_repair_references_only_failed_assets(services: Services) -> None:
    created = campaign(services)
    orchestrator = Orchestrator(services)
    failed = orchestrator.create_task(created.id, "capture", "capture-v1")
    repair = orchestrator.create_repair_task(created.id, failed, ["asset-bad"])
    assert repair.targeted_asset_ids == ["asset-bad"]
    assert repair.task_type == "repair:capture"
