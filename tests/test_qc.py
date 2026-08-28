from __future__ import annotations

import subprocess
from pathlib import Path

from adforge.models import (
    Asset,
    Campaign,
    CampaignState,
    CampaignTask,
    ProductTruthSnapshot,
    Render,
)
from adforge.qc import QCFinding, QCHook, QCPolicy, QCService, RepairPlanner, Severity
from adforge.renderer import EditSpec, FFmpegRenderer
from adforge.services import Services


def make_media(path: Path, *, valid: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not valid:
        path.write_bytes(b"broken video")
        return
    subprocess.run(  # noqa: S603
        [
            "/usr/bin/ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=360x640:d=6:r=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=6",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-pix_fmt",
            "yuv420p",
            "-shortest",
            str(path),
        ],
        check=True,
    )


def spec() -> EditSpec:
    return EditSpec.model_validate(
        {
            "campaign_id": "campaign",
            "clips": [
                {
                    "source": "generated/source.mp4",
                    "timeline_start_seconds": 0,
                    "source_out_seconds": 6,
                }
            ],
            "audio_tracks": [{"source": "audio/audio.wav", "kind": "MUSIC"}],
            "cta": {"text": "Learn more", "start_seconds": 4, "end_seconds": 6},
            "output_profile": {
                "aspect_ratio": "9:16",
                "duration_seconds": 6,
                "width": 360,
                "height": 640,
            },
            "output_path": "renders/drafts/draft.mp4",
        }
    )


def setup_qc(tmp_path: Path, *, valid: bool = True) -> tuple[
    Services, Campaign, Render, ProductTruthSnapshot, EditSpec
]:
    services = Services(tmp_path / "runtime", Path("schemas"))
    services.initialize()
    campaign = services.campaigns.save(
        Campaign(
            product_id="product",
            name="QC",
            brief="QC brief",
            state=CampaignState.QC,
            active=True,
        )
    )
    workspace = services.storage.campaign_workspace(campaign.id)
    edit_spec = spec().model_copy(update={"campaign_id": campaign.id})
    output = workspace / edit_spec.output_path
    make_media(output, valid=valid)
    render = services.renders.save(
        Render(
            campaign_id=campaign.id,
            status="COMPLETE",
            spec_path="edit/spec.json",
            output_path=edit_spec.output_path,
            aspect_ratio="9:16",
            duration_seconds=6,
        )
    )
    truth = {
        "approved_features": ["Organizes receipts"],
        "prohibited_claims": ["Guarantees reimbursement"],
    }
    snapshot = services.truth_snapshots.save(
        ProductTruthSnapshot(
            product_id="product",
            campaign_id=campaign.id,
            checksum="a" * 64,
            truth=truth,
            provenance=[
                {"claim": "Organizes receipts", "status": "CURRENT", "source": "APK"}
            ],
        )
    )
    return services, campaign, render, snapshot, edit_spec


def test_broken_video_false_claim_and_missing_asset_are_blockers(tmp_path: Path) -> None:
    services, campaign, render, snapshot, edit_spec = setup_qc(tmp_path, valid=False)
    result = QCService(services, FFmpegRenderer()).run(
        campaign,
        render,
        edit_spec,
        snapshot,
        claims=["Guarantees reimbursement"],
        required_asset_ids=["missing-asset"],
    )
    assert result.passed is False
    assert any("Invalid data" in item or "ffprobe" in item for item in result.blockers)
    assert any("prohibited claim" in item for item in result.blockers)
    assert any("missing-asset" in item for item in result.blockers)
    workspace = services.storage.campaign_workspace(campaign.id)
    assert (workspace / "qc" / f"qc-{result.id}.json").is_file()


class AdvisoryHook(QCHook):
    def inspect(
        self, campaign: Campaign, render: Render, spec: EditSpec
    ) -> list[QCFinding]:
        return [
            QCFinding(
                code="MINOR_FRAMING",
                severity=Severity.ADVISORY,
                message="Minor framing imperfection",
            )
        ]


def test_minor_advisory_passes_and_does_not_schedule_repair(tmp_path: Path) -> None:
    services, campaign, render, snapshot, edit_spec = setup_qc(tmp_path)
    result = QCService(services, FFmpegRenderer(), hooks=[AdvisoryHook()]).run(
        campaign,
        render,
        edit_spec,
        snapshot,
        claims=["Organizes receipts"],
        required_asset_ids=[],
    )
    assert result.passed is True
    assert result.advisories == ["Minor framing imperfection"]
    task = services.tasks.save(
        CampaignTask(campaign_id=campaign.id, task_type="draft-render", idempotency_key="d1")
    )
    plan = RepairPlanner(services).plan(campaign, task, result)
    assert plan.state == "NO_REPAIR"
    assert not services.tasks.find_by("task_type", "repair:draft-render")


def test_repair_targets_only_relevant_dependency_and_budget_controls_state(
    tmp_path: Path,
) -> None:
    services, campaign, render, snapshot, edit_spec = setup_qc(tmp_path, valid=False)
    asset = services.assets.save(
        Asset(
            campaign_id=campaign.id,
            asset_type="video",
            status="MISSING",
            filepath="generated/missing.mp4",
        )
    )
    result = QCService(services, FFmpegRenderer()).run(
        campaign,
        render,
        edit_spec,
        snapshot,
        claims=[],
        required_asset_ids=[asset.id],
    )
    failed_task = services.tasks.save(
        CampaignTask(campaign_id=campaign.id, task_type="draft-render", idempotency_key="d1")
    )
    planner = RepairPlanner(
        services, QCPolicy(max_targeted_repairs_per_task=1)
    )
    first = planner.plan(campaign, failed_task, result)
    assert set(first.targeted_asset_ids) == {render.id, asset.id}
    current = services.campaigns.get(campaign.id)
    assert current is not None and current.state == CampaignState.REPAIR
    current = services.campaigns.save(current.model_copy(update={"state": CampaignState.QC}))
    exhausted = planner.plan(current, failed_task, result)
    assert exhausted.state == "BUDGET_EXHAUSTED"
    blocked = services.campaigns.get(campaign.id)
    assert blocked is not None and blocked.state == CampaignState.BLOCKED
