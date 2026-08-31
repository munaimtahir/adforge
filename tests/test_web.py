from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adforge.auth import hash_password
from adforge.campaign_stages import FINAL_RENDER_RELATIVE_PATH
from adforge.models import (
    Campaign,
    CampaignState,
    CampaignTask,
    LedgerEvent,
    Product,
    QCResult,
    Render,
    TaskState,
    TruthReadiness,
    WorkerJob,
    WorkerJobStatus,
    WorkerNode,
    WorkerStatus,
)
from adforge.storage import sha256_file
from adforge.web import WebContext, create_app

PASSWORD = "fixture-password-123"  # noqa: S105
SECRET = "fixture-secret-key-that-is-long-enough-123"  # noqa: S105


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    imports = tmp_path / "imports"
    imports.mkdir()
    app = create_app(
        runtime_root=tmp_path / "runtime",
        schema_root=Path("schemas"),
        secret_key=SECRET,
        password_hash=hash_password(PASSWORD, salt=b"0123456789abcdef"),
        import_root=imports,
        secure_cookie=False,
    )
    context: WebContext = app.state.context
    context.services.products.save(
        Product(
            id="product-1",
            name="Fixture Product",
            slug="fixture-product",
            truth_readiness=TruthReadiness.READY,
        )
    )
    return TestClient(app)


def login(client: TestClient) -> str:
    response = client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert response.status_code == 303
    page = client.get("/")
    assert page.status_code == 200
    match = re.search(r'name="csrf" value="([^"]+)"', page.text)
    assert match is not None
    return match.group(1)


def test_authentication_required_and_invalid_login_rejected(client: TestClient) -> None:
    assert client.get("/", follow_redirects=False).status_code == 303
    bad = client.post("/login", data={"password": "wrong-password"})
    assert bad.status_code == 200
    assert "Invalid credentials" in bad.text
    login(client)
    assert "Make the ad" in client.get("/").text


def test_required_routes_render_without_secret_leakage(client: TestClient) -> None:
    login(client)
    routes = (
        "/",
        "/products",
        "/products/new",
        "/products/product-1",
        "/campaigns/new",
        "/campaigns",
        "/outputs",
        "/settings",
    )
    for route in routes:
        response = client.get(route)
        assert response.status_code == 200, route
        assert SECRET not in response.text
        assert "fixture-password" not in response.text


def test_campaign_creation_path_validation_and_state_visibility(
    client: TestClient, tmp_path: Path
) -> None:
    csrf = login(client)
    rejected = client.post(
        "/campaigns",
        data={
            "csrf": csrf,
            "product_id": "product-1",
            "name": "Launch",
            "brief": "Use approved proof",
            "apk_path": "/etc/passwd",
        },
    )
    assert rejected.status_code == 422
    import_root = client.app.state.context.import_root
    apk = import_root / "fixture.apk"
    apk.write_bytes(b"fixture")
    created = client.post(
        "/campaigns",
        data={
            "csrf": csrf,
            "product_id": "product-1",
            "name": "Launch",
            "brief": "Use approved proof",
            "apk_path": str(apk),
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    detail = client.get(created.headers["location"])
    assert "CREATED" in detail.text
    assert "Use approved proof" in detail.text


def test_campaign_creation_accepts_a_real_apk_file_upload(client: TestClient) -> None:
    csrf = login(client)
    empty = client.post(
        "/campaigns",
        data={
            "csrf": csrf,
            "product_id": "product-1",
            "name": "Launch",
            "brief": "Use approved proof",
        },
        files=[("apk_file", ("app.apk", b"", "application/vnd.android.package-archive"))],
    )
    assert empty.status_code == 422

    not_apk = client.post(
        "/campaigns",
        data={
            "csrf": csrf,
            "product_id": "product-1",
            "name": "Launch",
            "brief": "Use approved proof",
        },
        files=[("apk_file", ("app.txt", b"not an apk", "text/plain"))],
    )
    assert not_apk.status_code == 422

    created = client.post(
        "/campaigns",
        data={
            "csrf": csrf,
            "product_id": "product-1",
            "name": "Launch",
            "brief": "Use approved proof",
        },
        files=[
            (
                "apk_file",
                ("app.apk", b"real-apk-bytes", "application/vnd.android.package-archive"),
            )
        ],
        follow_redirects=False,
    )
    assert created.status_code == 303
    campaign_id = created.headers["location"].rsplit("/", 1)[-1]
    context: WebContext = client.app.state.context
    workspace = context.services.storage.campaign_workspace(campaign_id)
    ingested = workspace / "app-capture" / "source.apk"
    assert ingested.is_file()
    assert ingested.read_bytes() == b"real-apk-bytes"


def test_get_products_new_renders_the_form_not_a_404(client: TestClient) -> None:
    """The real bug found live: `/products/{product_id}` was registered before
    `/products/new`, so FastAPI/Starlette (route matching is registration-order,
    not specificity-order) matched "new" as a product_id first -- product_detail
    then 404'd since no such product exists. `/products/new` never rendered.
    """
    csrf = login(client)
    response = client.get("/products/new")
    assert response.status_code == 200
    assert "product_truth_json" in response.text
    assert csrf in response.text


def test_new_product_form_creates_a_ready_product_from_pasted_truth(
    client: TestClient,
) -> None:
    csrf = login(client)
    truth = json.dumps(
        {
            "product_id": "second-product",
            "product_name": "Second Product",
            "approved_features": ["Does the fictional thing"],
            "prohibited_claims": ["Guarantees anything"],
            "evidence": [
                {
                    "claim": "Does the fictional thing",
                    "status": "CURRENT",
                    "source": "APK",
                }
            ],
            "last_verified_at": "2026-08-29T00:00:00Z",
        }
    )
    created = client.post(
        "/products",
        data={
            "csrf": csrf,
            "name": "Second Product",
            "slug": "second-product",
            "product_truth_json": truth,
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    context: WebContext = client.app.state.context
    product = context.services.products.get("second-product")
    assert product is not None
    assert product.truth_readiness == TruthReadiness.READY
    detail = client.get(created.headers["location"])
    assert "Second Product" in detail.text
    assert "READY" in detail.text


def test_new_product_form_rejects_invalid_truth_json(client: TestClient) -> None:
    csrf = login(client)
    rejected = client.post(
        "/products",
        data={
            "csrf": csrf,
            "name": "Broken",
            "slug": "broken-product",
            "product_truth_json": "not json",
        },
    )
    assert rejected.status_code == 200
    assert "invalid JSON" in rejected.text
    context: WebContext = client.app.state.context
    assert context.services.products.get("broken-product") is None


def test_manual_worker_job_completion_uploads_artifact_and_resumes(
    client: TestClient,
) -> None:
    csrf = login(client)
    context: WebContext = client.app.state.context
    campaign = context.services.campaigns.save(
        Campaign(
            product_id="product-1",
            name="Manual completion",
            brief="brief",
            state=CampaignState.WAITING_FOR_WORKER,
            resume_state=CampaignState.ASSET_GENERATION,
            active=False,
        )
    )
    job = context.services.worker_jobs.save(
        WorkerJob(
            campaign_id=campaign.id,
            capability="flow_generation",
            payload={
                "prompt": "A fictional demo clip",
                "output_filename": "scene.mp4",
                "scene_id": "scene-1",
            },
            idempotency_key="manual-test-key",
        )
    )

    response = client.post(
        f"/worker-jobs/{job.id}/manual-complete",
        data={"csrf": csrf},
        files=[("files", ("scene.mp4", b"not-empty-video-bytes", "video/mp4"))],
        follow_redirects=False,
    )

    assert response.status_code == 303
    updated_job = context.services.worker_jobs.get(job.id)
    assert updated_job is not None
    assert updated_job.status == WorkerJobStatus.COMPLETE
    assert updated_job.worker_id == "manual-admin-worker"
    assets = context.services.assets.find_by("campaign_id", campaign.id)
    assert len(assets) == 1
    assert assets[0].source == "worker:flow_generation"
    assert assets[0].asset_type == "generated_video"


def test_manual_worker_job_completion_accepts_real_browser_download_filenames(
    client: TestClient,
) -> None:
    """The exact real bug found live: a real Flow-downloaded file's browser
    filename (spaces, parentheses -- ordinary download naming, e.g. what Chrome
    names a second download of the same title) doesn't satisfy
    safe_component()'s strict pattern, and store_artifact previously raised that
    UnsafePathError straight into an uncaught 500.
    """
    csrf = login(client)
    context: WebContext = client.app.state.context
    campaign = context.services.campaigns.save(
        Campaign(product_id="product-1", name="Real filename", brief="brief")
    )
    job = context.services.worker_jobs.save(
        WorkerJob(
            campaign_id=campaign.id,
            capability="flow_generation",
            payload={
                "prompt": "A fictional demo clip",
                "output_filename": "scene.mp4",
                "scene_id": "scene-1",
            },
            idempotency_key="manual-real-filename-key",
        )
    )

    response = client.post(
        f"/worker-jobs/{job.id}/manual-complete",
        data={"csrf": csrf},
        files=[
            (
                "files",
                ("DemoTask intro (Google Flow) (1).mp4", b"real-download-bytes", "video/mp4"),
            )
        ],
        follow_redirects=False,
    )

    assert response.status_code == 303
    updated_job = context.services.worker_jobs.get(job.id)
    assert updated_job is not None
    assert updated_job.status == WorkerJobStatus.COMPLETE
    artifacts = context.services.worker_artifacts.find_by("job_id", job.id)
    assert len(artifacts) == 1
    assert artifacts[0].filename == "scene.mp4"


def test_manual_worker_job_completion_works_after_automated_attempts_exhausted(
    client: TestClient,
) -> None:
    """The exact real scenario found live: a flow_generation job that exhausted
    its 3-attempt automated budget (status FAILED, attempt == max_attempts) --
    the operator manually generating and uploading the asset must still be able
    to complete it.
    """
    csrf = login(client)
    context: WebContext = client.app.state.context
    campaign = context.services.campaigns.save(
        Campaign(
            product_id="product-1",
            name="Manual after exhaustion",
            brief="brief",
            state=CampaignState.WAITING_FOR_WORKER,
            resume_state=CampaignState.ASSET_GENERATION,
            active=False,
        )
    )
    job = context.services.worker_jobs.save(
        WorkerJob(
            campaign_id=campaign.id,
            capability="flow_generation",
            status=WorkerJobStatus.FAILED,
            attempt=3,
            max_attempts=3,
            failure_summary="Locator.fill: Timeout 30000ms exceeded.",
            payload={
                "prompt": "A fictional demo clip",
                "output_filename": "scene.mp4",
                "scene_id": "scene-1",
            },
            idempotency_key="manual-exhausted-key",
        )
    )

    response = client.post(
        f"/worker-jobs/{job.id}/manual-complete",
        data={"csrf": csrf},
        files=[("files", ("scene.mp4", b"not-empty-video-bytes", "video/mp4"))],
        follow_redirects=False,
    )

    assert response.status_code == 303
    updated_job = context.services.worker_jobs.get(job.id)
    assert updated_job is not None
    assert updated_job.status == WorkerJobStatus.COMPLETE
    assert updated_job.attempt == 3
    assets = context.services.assets.find_by("campaign_id", campaign.id)
    assert len(assets) == 1


def test_manual_worker_job_completion_works_when_campaign_already_blocked(
    client: TestClient,
) -> None:
    """The exact real bug found live, one step worse than the FAILED-job case
    above: by the time a flow_generation job exhausts its automated attempt
    budget, WorkerJobService.fail() has already moved the *campaign* itself
    WAITING_FOR_WORKER -> BLOCKED (capturing resume_state=WAITING_FOR_WORKER) --
    that's the real state a stuck campaign is actually found in, not
    WAITING_FOR_WORKER directly. complete()'s own artifact-import-and-continue
    logic only fires when the campaign is WAITING_FOR_WORKER at that exact
    moment, so completing the job while BLOCKED silently skipped artifact
    import entirely: both real DemoTask WorkerJobs showed COMPLETE with real
    uploaded artifacts, yet zero Asset records existed and the campaign never
    moved -- no error, just silently stuck.
    """
    csrf = login(client)
    context: WebContext = client.app.state.context
    campaign = context.services.campaigns.save(
        Campaign(
            product_id="product-1",
            name="Manual while blocked",
            brief="brief",
            state=CampaignState.BLOCKED,
            resume_state=CampaignState.WAITING_FOR_WORKER,
            active=False,
        )
    )
    job = context.services.worker_jobs.save(
        WorkerJob(
            campaign_id=campaign.id,
            capability="flow_generation",
            status=WorkerJobStatus.FAILED,
            attempt=3,
            max_attempts=3,
            failure_summary="Locator.fill: Timeout 30000ms exceeded.",
            payload={
                "prompt": "A fictional demo clip",
                "output_filename": "scene.mp4",
                "scene_id": "scene-1",
            },
            idempotency_key="manual-blocked-key",
        )
    )

    response = client.post(
        f"/worker-jobs/{job.id}/manual-complete",
        data={"csrf": csrf},
        files=[("files", ("scene.mp4", b"not-empty-video-bytes", "video/mp4"))],
        follow_redirects=False,
    )

    assert response.status_code == 303
    updated_job = context.services.worker_jobs.get(job.id)
    assert updated_job is not None
    assert updated_job.status == WorkerJobStatus.COMPLETE
    assets = context.services.assets.find_by("campaign_id", campaign.id)
    assert len(assets) == 1
    assert assets[0].source == "worker:flow_generation"


def test_manual_worker_job_completion_rejects_empty_upload(client: TestClient) -> None:
    csrf = login(client)
    context: WebContext = client.app.state.context
    campaign = context.services.campaigns.save(
        Campaign(product_id="product-1", name="Manual empty", brief="brief")
    )
    job = context.services.worker_jobs.save(
        WorkerJob(
            campaign_id=campaign.id,
            capability="flow_generation",
            payload={"prompt": "x", "output_filename": "scene.mp4", "scene_id": "scene-1"},
            idempotency_key="manual-empty-key",
        )
    )

    response = client.post(
        f"/worker-jobs/{job.id}/manual-complete",
        data={"csrf": csrf},
        files=[("files", ("scene.mp4", b"", "video/mp4"))],
    )

    assert response.status_code == 422


def test_retry_resets_the_same_task_row_so_campaign_worker_will_find_it(
    client: TestClient,
) -> None:
    """The real bug found live: retry used to create a new CampaignTask row under
    a "...:manual-retry:N" key, but CampaignWorker.run() only ever looks up (and
    creates, if missing) the task at its own deterministic key -- so the retried
    row was never discovered and the "Retry" button silently did nothing for any
    CampaignWorker-driven stage. Retrying must reset the *same* row instead.
    """
    csrf = login(client)
    context: WebContext = client.app.state.context
    campaign = context.services.campaigns.save(
        Campaign(
            product_id="product-1",
            name="Retry test",
            brief="brief",
            state=CampaignState.BLOCKED,
            resume_state=CampaignState.STRATEGY,
            active=False,
        )
    )
    task = context.services.tasks.save(
        CampaignTask(
            campaign_id=campaign.id,
            task_type="strategy",
            idempotency_key="stage:strategy:v1",
            state=TaskState.BLOCKED,
            attempt=3,
            max_attempts=3,
            failure_summary="boom",
        )
    )

    response = client.post(
        f"/tasks/{task.id}/retry", data={"csrf": csrf}, follow_redirects=False
    )

    assert response.status_code == 303
    all_tasks = context.services.tasks.find_by("campaign_id", campaign.id)
    assert len(all_tasks) == 1
    assert all_tasks[0].id == task.id
    assert all_tasks[0].state == TaskState.PENDING
    assert all_tasks[0].attempt == 0
    assert all_tasks[0].failure_summary is None
    assert all_tasks[0].idempotency_key == "stage:strategy:v1"


def test_start_on_a_campaign_already_past_created_returns_409_not_500(
    client: TestClient,
) -> None:
    """Regression found live: a slow first "Start" (the synchronous pipeline
    run inside this request can take minutes for real AI calls) can still be
    executing server-side after the client gives up on a proxy timeout. A
    second click then hits a campaign already past CREATED (e.g. BLOCKED at
    STRATEGY) and used to raise an unhandled TransitionError straight into a
    500 instead of a friendly, actionable response.
    """
    csrf = login(client)
    context: WebContext = client.app.state.context
    campaign = context.services.campaigns.save(
        Campaign(
            product_id="product-1",
            name="Already started",
            brief="brief",
            state=CampaignState.BLOCKED,
            resume_state=CampaignState.STRATEGY,
            active=False,
        )
    )

    response = client.post(
        f"/campaigns/{campaign.id}/start", data={"csrf": csrf}, follow_redirects=False
    )

    assert response.status_code == 409
    assert "illegal transition" in response.text


def test_one_active_campaign_is_enforced_in_ux_and_action(client: TestClient) -> None:
    csrf = login(client)
    context: WebContext = client.app.state.context
    first = context.services.campaigns.save(
        Campaign(product_id="product-1", name="First", brief="First brief", active=True)
    )
    page = client.get("/campaigns/new")
    assert "holds the active production lease" in page.text
    assert "disabled" in page.text
    response = client.post(
        "/campaigns",
        data={
            "csrf": csrf,
            "product_id": "product-1",
            "name": "Second",
            "brief": "Second brief",
            "apk_path": "",
        },
    )
    assert response.status_code == 409
    assert context.services.campaigns.get(first.id) is not None


def test_csrf_is_required_for_state_changes(client: TestClient) -> None:
    login(client)
    response = client.post(
        "/campaigns",
        data={
            "csrf": "tampered",
            "product_id": "product-1",
            "name": "Campaign",
            "brief": "Brief",
            "apk_path": "",
        },
    )
    assert response.status_code == 403


def test_campaign_detail_shows_the_full_pipeline_before_any_stage_starts(
    client: TestClient,
) -> None:
    """Regression: the old "Timeline" card only listed `CampaignTask` rows
    that already existed, so a freshly created campaign showed an empty
    list -- an operator had no way to see the pipeline's shape until stages
    started completing one by one. Every stage must render up front.
    """
    login(client)
    context: WebContext = client.app.state.context
    campaign = context.services.campaigns.save(
        Campaign(product_id="product-1", name="Fresh", brief="brief")
    )

    page = client.get(f"/campaigns/{campaign.id}")

    assert page.status_code == 200
    for stage in ("STRATEGY", "STORYBOARD", "APP_CAPTURE", "FINAL_RENDER", "EXPORT"):
        assert stage in page.text
    assert "NOT STARTED" in page.text


def test_campaign_detail_shows_inline_retry_on_the_blocked_stage_row(
    client: TestClient,
) -> None:
    login(client)
    context: WebContext = client.app.state.context
    campaign = context.services.campaigns.save(
        Campaign(
            product_id="product-1",
            name="Blocked stage",
            brief="brief",
            state=CampaignState.BLOCKED,
            resume_state=CampaignState.STRATEGY,
        )
    )
    context.services.tasks.save(
        CampaignTask(
            campaign_id=campaign.id,
            task_type="strategy",
            idempotency_key="stage:strategy:v1",
            state=TaskState.BLOCKED,
            attempt=3,
            max_attempts=3,
            failure_summary="provider unavailable",
        )
    )

    page = client.get(f"/campaigns/{campaign.id}")

    assert page.status_code == 200
    assert "provider unavailable" in page.text
    assert 'action="/tasks/' in page.text


def test_campaign_detail_shows_manual_complete_form_inline_on_its_stage(
    client: TestClient,
) -> None:
    login(client)
    context: WebContext = client.app.state.context
    campaign = context.services.campaigns.save(
        Campaign(product_id="product-1", name="Waiting on Flow", brief="brief")
    )
    task = context.services.tasks.save(
        CampaignTask(
            campaign_id=campaign.id,
            task_type="asset_generation",
            idempotency_key="stage:asset_generation:v1",
        )
    )
    context.services.worker_jobs.save(
        WorkerJob(
            campaign_id=campaign.id,
            task_id=task.id,
            capability="flow_generation",
            payload={"prompt": "A fictional demo clip", "output_filename": "scene.mp4"},
            idempotency_key="manual-inline-key",
        )
    )

    page = client.get(f"/campaigns/{campaign.id}")

    assert page.status_code == 200
    assert "A fictional demo clip" in page.text
    assert "/worker-jobs/" in page.text
    assert "Upload &amp; complete" in page.text


def test_export_download_serves_the_canonical_export_not_the_workspace_copy(
    client: TestClient,
) -> None:
    login(client)
    context: WebContext = client.app.state.context
    campaign = context.services.campaigns.save(
        Campaign(product_id="product-1", name="Exported", brief="brief")
    )
    content = b"real-final-mp4-bytes"
    export_dir = context.services.storage.root / "exports" / campaign.id
    export_dir.mkdir(parents=True)
    export_path = export_dir / "final.mp4"
    export_path.write_bytes(content)
    context.services.renders.save(
        Render(
            campaign_id=campaign.id,
            status="COMPLETE",
            spec_path="edit/edit_plan.v2.json",
            output_path=FINAL_RENDER_RELATIVE_PATH,
            aspect_ratio="9:16",
            duration_seconds=20,
            checksum=sha256_file(export_path),
        )
    )

    response = client.get(f"/campaigns/{campaign.id}/export/download")

    assert response.status_code == 200
    assert response.content == content


def test_export_download_rejects_a_checksum_mismatch_instead_of_serving_stale_bytes(
    client: TestClient,
) -> None:
    login(client)
    context: WebContext = client.app.state.context
    campaign = context.services.campaigns.save(
        Campaign(product_id="product-1", name="Corrupted export", brief="brief")
    )
    export_dir = context.services.storage.root / "exports" / campaign.id
    export_dir.mkdir(parents=True)
    (export_dir / "final.mp4").write_bytes(b"tampered-bytes")
    context.services.renders.save(
        Render(
            campaign_id=campaign.id,
            status="COMPLETE",
            spec_path="edit/edit_plan.v2.json",
            output_path=FINAL_RENDER_RELATIVE_PATH,
            aspect_ratio="9:16",
            duration_seconds=20,
            checksum="0" * 64,
        )
    )

    response = client.get(f"/campaigns/{campaign.id}/export/download")

    assert response.status_code == 500


def test_export_download_404s_when_no_final_render_exists(client: TestClient) -> None:
    login(client)
    context: WebContext = client.app.state.context
    campaign = context.services.campaigns.save(
        Campaign(product_id="product-1", name="Not exported", brief="brief")
    )

    response = client.get(f"/campaigns/{campaign.id}/export/download")

    assert response.status_code == 404


def make_clip(path: Path, *, duration: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(  # noqa: S603, S607 - test fixture, fixed argv
        [
            "/usr/bin/ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c=blue:s=640x360:d={duration}:r=30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )


def test_outputs_page_shows_qc_and_probe_metadata_for_the_final_export(
    client: TestClient,
) -> None:
    login(client)
    context: WebContext = client.app.state.context
    campaign = context.services.campaigns.save(
        Campaign(product_id="product-1", name="Probed export", brief="brief")
    )
    export_dir = context.services.storage.root / "exports" / campaign.id
    export_path = export_dir / "final.mp4"
    make_clip(export_path, duration=2)
    render_record = context.services.renders.save(
        Render(
            campaign_id=campaign.id,
            status="COMPLETE",
            spec_path="edit/edit_plan.v2.json",
            output_path=FINAL_RENDER_RELATIVE_PATH,
            aspect_ratio="9:16",
            duration_seconds=2,
            checksum=sha256_file(export_path),
        )
    )
    context.services.qc_results.save(
        QCResult(
            campaign_id=campaign.id,
            render_id=render_record.id,
            passed=True,
            blockers=[],
            advisories=["minor pacing note"],
        )
    )

    page = client.get("/outputs")

    assert page.status_code == 200
    assert "Probed export" in page.text
    assert "PASSED" in page.text
    assert "h264" in page.text
    assert f"/campaigns/{campaign.id}/export/download" in page.text


def test_worker_activity_shows_current_action_and_recent_events(client: TestClient) -> None:
    login(client)
    context: WebContext = client.app.state.context
    campaign = context.services.campaigns.save(
        Campaign(product_id="product-1", name="Watched campaign", brief="brief")
    )
    node = context.services.worker_nodes.save(
        WorkerNode(
            name="adforge-linux-01",
            agent_version="0.1.0",
            os="Linux",
            architecture="x86_64",
            status=WorkerStatus.ONLINE,
            capabilities=["android_capture"],
            active_job_id="job-123",
        )
    )
    context.services.ledger_events.save(
        LedgerEvent(
            campaign_id=campaign.id,
            stage="WORKER_JOB",
            event_type="worker_job_progress",
            status="RUNNING",
            details={
                "job_id": "job-123",
                "worker_id": node.id,
                "detail": "action 14/38: TAP_TEXT Purchase Date",
            },
        )
    )

    page = client.get("/worker-activity")

    assert page.status_code == 200
    assert "adforge-linux-01" in page.text
    assert "action 14/38: TAP_TEXT Purchase Date" in page.text
    assert "Watched campaign" in page.text
