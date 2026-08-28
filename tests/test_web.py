from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adforge.auth import hash_password
from adforge.models import (
    Campaign,
    CampaignState,
    CampaignTask,
    Product,
    TaskState,
    TruthReadiness,
    WorkerJob,
    WorkerJobStatus,
)
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
