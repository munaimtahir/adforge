from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from adforge.models import Campaign, CampaignState, CampaignTask, Product, TruthReadiness
from adforge.services import Services
from adforge.worker import CampaignWorker, StageHandler


def test_fixture_pipeline_waits_resumes_repairs_restarts_and_completes(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    services = Services(root, Path("schemas"))
    services.initialize()
    product = services.products.save(
        Product(
            name="Verified Fixture",
            slug="verified-fixture",
            truth_readiness=TruthReadiness.READY,
        )
    )
    campaign = services.campaigns.save(
        Campaign(product_id=product.id, name="Fixture acceptance", brief="Verified brief")
    )
    calls: Counter[CampaignState] = Counter()

    def handler(
        current: Campaign, task: CampaignTask, attempt: int
    ) -> dict[str, Any]:
        calls[current.state] += 1
        return {"stage": current.state, "task_id": task.id, "attempt": attempt}

    def generation(
        current: Campaign, task: CampaignTask, attempt: int
    ) -> dict[str, Any]:
        calls[current.state] += 1
        return {
            "waiting_state": CampaignState.WAITING_FOR_EXTERNAL_ASSET,
            "reason": "generation handoff exported",
        }

    qc_runs = 0

    def qc(current: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        nonlocal qc_runs
        calls[current.state] += 1
        qc_runs += 1
        return {
            "next_state": CampaignState.REPAIR if qc_runs == 1 else CampaignState.FINAL_RENDER,
            "induced_failure": qc_runs == 1,
        }

    handlers: dict[CampaignState, StageHandler] = {
        state: handler
        for state in (
            CampaignState.PRODUCT_TRUTH_VALIDATION,
            CampaignState.STRATEGY,
            CampaignState.SCRIPT,
            CampaignState.STORYBOARD,
            CampaignState.ASSET_PLAN,
            CampaignState.APP_CAPTURE,
            CampaignState.AUDIO_PRODUCTION,
            CampaignState.EDIT_PLAN,
            CampaignState.DRAFT_RENDER,
            CampaignState.REPAIR,
            CampaignState.FINAL_RENDER,
            CampaignState.EXPORT,
        )
    }
    handlers[CampaignState.ASSET_GENERATION] = generation
    handlers[CampaignState.QC] = qc
    first = CampaignWorker(services, handlers).run(campaign.id, max_stages=3)
    assert first.state == CampaignState.STORYBOARD
    assert calls[CampaignState.PRODUCT_TRUTH_VALIDATION] == 1
    restarted_services = Services(root, Path("schemas"))
    restarted_services.initialize()
    second = CampaignWorker(restarted_services, handlers).run(campaign.id)
    assert second.state == CampaignState.WAITING_FOR_EXTERNAL_ASSET
    assert calls[CampaignState.PRODUCT_TRUTH_VALIDATION] == 1
    resumed = CampaignWorker(restarted_services, handlers).orchestrator.resume(campaign.id)
    assert resumed.state == CampaignState.ASSET_GENERATION
    final = CampaignWorker(restarted_services, handlers).run(campaign.id)
    assert final.state == CampaignState.COMPLETE
    assert qc_runs == 2
    assert calls[CampaignState.REPAIR] == 1
    generation_tasks = restarted_services.tasks.find_by(
        "task_type", CampaignState.ASSET_GENERATION.value.lower()
    )
    assert len(generation_tasks) == 1
    assert calls[CampaignState.ASSET_GENERATION] == 1
