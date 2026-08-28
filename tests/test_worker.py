from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adforge.auth import hash_password
from adforge.database import Database
from adforge.models import (
    Campaign,
    CampaignState,
    WorkerErrorClass,
    WorkerJobStatus,
    WorkerNode,
    WorkerStatus,
)
from adforge.orchestrator import Orchestrator
from adforge.services import Services
from adforge.web import WebContext, create_app
from adforge.worker_api import (
    WorkerConflictError,
    WorkerJobService,
    WorkerNotFoundError,
    WorkerProtocolError,
)
from adforge.worker_auth import WorkerAuthError, authenticate, issue_token

PASSWORD = "fixture-password-123"  # noqa: S105
SECRET = "fixture-secret-key-that-is-long-enough-123"  # noqa: S105


@pytest.fixture
def services(tmp_path: Path) -> Services:
    value = Services(tmp_path / "runtime", Path("schemas"))
    value.initialize()
    return value


def make_worker(
    services: Services, name: str = "laptop-1", capabilities: list[str] | None = None
) -> tuple[WorkerNode, str]:
    worker = services.worker_nodes.save(
        WorkerNode(
            name=name,
            agent_version="0.1.0",
            os="Windows",
            architecture="x86_64",
            capabilities=capabilities or ["synthetic_echo"],
        )
    )
    token = issue_token(services, worker)
    return worker, token


def waiting_campaign(services: Services) -> Campaign:
    campaign = services.campaigns.save(
        Campaign(product_id="product-1", name="Waiting", brief="brief")
    )
    return Orchestrator(services).transition(campaign.id, CampaignState.WAITING_FOR_WORKER)


def test_token_authenticates_and_rejects_tampered_or_revoked(services: Services) -> None:
    worker, token = make_worker(services)
    assert authenticate(services, token).id == worker.id
    with pytest.raises(WorkerAuthError):
        authenticate(services, token[:-1] + ("0" if token[-1] != "0" else "1"))
    with pytest.raises(WorkerAuthError):
        authenticate(services, None)
    for record in services.worker_tokens.find_by("worker_id", worker.id):
        services.worker_tokens.save(record.model_copy(update={"revoked": True}))
    with pytest.raises(WorkerAuthError):
        authenticate(services, token)


def test_exclusive_lease_under_concurrent_claim(services: Services) -> None:
    campaign = waiting_campaign(services)
    job_service = WorkerJobService(services)
    job = job_service.create_job(campaign.id, "synthetic_echo", {"n": 1}, "job-1")
    worker_a, _ = make_worker(services, "a")
    worker_b, _ = make_worker(services, "b")
    claimed_a = job_service.claim(worker_a)
    claimed_b = job_service.claim(worker_b)
    assert claimed_a is not None
    assert claimed_a.id == job.id
    assert claimed_b is None


def test_synthetic_round_trip_completes_and_resumes_waiting_campaign(services: Services) -> None:
    campaign = waiting_campaign(services)
    job_service = WorkerJobService(services)
    job = job_service.create_job(campaign.id, "synthetic_echo", {"hello": "world"}, "job-2")
    worker, _ = make_worker(services)
    claimed = job_service.claim(worker)
    assert claimed is not None
    renewed = job_service.renew_lease(worker, claimed.id)
    assert renewed.status == WorkerJobStatus.RUNNING
    job_service.record_progress(worker, claimed.id, "working")
    artifact = job_service.store_artifact(
        worker,
        claimed.id,
        filename="echo.json",
        content=b'{"hello":"world"}',
        content_type="application/json",
        declared_checksum=hashlib.sha256(b'{"hello":"world"}').hexdigest(),
    )
    assert artifact.filename == "echo.json"
    completed = job_service.complete(worker, claimed.id)
    assert completed.status == WorkerJobStatus.COMPLETE
    again = job_service.complete(worker, claimed.id)
    assert again.status == WorkerJobStatus.COMPLETE
    attempts = [a for a in services.worker_job_attempts.list() if a.job_id == job.id]
    complete_attempts = [a for a in attempts if a.status == WorkerJobStatus.COMPLETE]
    assert len(complete_attempts) == 1
    resumed = services.campaigns.get(campaign.id)
    assert resumed is not None
    assert resumed.state == CampaignState.CREATED
    assert resumed.active is True


def test_artifact_checksum_mismatch_is_rejected(services: Services) -> None:
    campaign = waiting_campaign(services)
    job_service = WorkerJobService(services)
    job_service.create_job(campaign.id, "synthetic_echo", {}, "job-3")
    worker, _ = make_worker(services)
    claimed = job_service.claim(worker)
    assert claimed is not None
    with pytest.raises(WorkerProtocolError, match="checksum"):
        job_service.store_artifact(
            worker,
            claimed.id,
            filename="bad.json",
            content=b"data",
            content_type="application/json",
            declared_checksum="0" * 64,
        )


def test_lease_expiry_reclaims_pending_and_then_fails_after_budget(services: Services) -> None:
    campaign = waiting_campaign(services)
    job_service = WorkerJobService(services, lease_seconds=-1)
    job = job_service.create_job(campaign.id, "synthetic_echo", {}, "job-4")
    worker, _ = make_worker(services)
    for _ in range(3):
        claimed = job_service.claim(worker)
        assert claimed is not None
        assert claimed.lease_expires_at is not None
        assert claimed.lease_expires_at < datetime.now(UTC)
        reclaimed = job_service.reclaim_expired()
        assert len(reclaimed) == 1
    final = services.worker_jobs.get(job.id)
    assert final is not None
    assert final.status == WorkerJobStatus.FAILED


def test_retryable_failure_requeues_then_blocks_campaign_after_budget(services: Services) -> None:
    campaign = waiting_campaign(services)
    job_service = WorkerJobService(services)
    job_service.create_job(campaign.id, "synthetic_echo", {}, "job-5")
    worker, _ = make_worker(services)
    last = None
    for _ in range(3):
        claimed = job_service.claim(worker)
        assert claimed is not None
        last = job_service.fail(
            worker, claimed.id, error_class=WorkerErrorClass.RETRYABLE, detail="boom"
        )
    assert last is not None
    assert last.status == WorkerJobStatus.FAILED
    blocked_campaign = services.campaigns.get(campaign.id)
    assert blocked_campaign is not None
    assert blocked_campaign.state == CampaignState.BLOCKED


def test_non_retryable_failure_blocks_immediately(services: Services) -> None:
    campaign = waiting_campaign(services)
    job_service = WorkerJobService(services)
    job_service.create_job(campaign.id, "synthetic_echo", {}, "job-6")
    worker, _ = make_worker(services)
    claimed = job_service.claim(worker)
    assert claimed is not None
    failed = job_service.fail(
        worker, claimed.id, error_class=WorkerErrorClass.NON_RETRYABLE, detail="unfixable"
    )
    assert failed.status == WorkerJobStatus.FAILED
    blocked_campaign = services.campaigns.get(campaign.id)
    assert blocked_campaign is not None
    assert blocked_campaign.state == CampaignState.BLOCKED


def test_offline_worker_heartbeat_timeout_does_not_touch_campaign(services: Services) -> None:
    job_service = WorkerJobService(services, heartbeat_timeout_seconds=0)
    worker, _ = make_worker(services)
    heartbeated = job_service.heartbeat(worker, capabilities=["synthetic_echo"], metadata={})
    assert heartbeated.status == WorkerStatus.ONLINE
    stale = heartbeated.model_copy(
        update={"last_heartbeat_at": datetime.now(UTC) - timedelta(seconds=5)}
    )
    services.worker_nodes.save(stale)
    job_service.sweep_offline()
    offline = services.worker_nodes.get(worker.id)
    assert offline is not None
    assert offline.status == WorkerStatus.OFFLINE


def test_cross_worker_job_and_artifact_access_is_rejected(services: Services) -> None:
    campaign = waiting_campaign(services)
    job_service = WorkerJobService(services)
    job_service.create_job(campaign.id, "synthetic_echo", {}, "job-7")
    owner, _ = make_worker(services, "owner")
    intruder, _ = make_worker(services, "intruder")
    claimed = job_service.claim(owner)
    assert claimed is not None
    with pytest.raises(WorkerConflictError):
        job_service.complete(intruder, claimed.id)
    with pytest.raises(WorkerConflictError):
        job_service.store_artifact(
            intruder,
            claimed.id,
            filename="x.json",
            content=b"{}",
            content_type="application/json",
            declared_checksum=hashlib.sha256(b"{}").hexdigest(),
        )


def test_service_restart_persists_and_reclaims_in_flight_jobs(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    first = Services(root, Path("schemas"))
    first.initialize()
    campaign = waiting_campaign(first)
    job_service = WorkerJobService(first, lease_seconds=-1)
    job_service.create_job(campaign.id, "synthetic_echo", {}, "job-8")
    worker, _ = make_worker(first)
    claimed = job_service.claim(worker)
    assert claimed is not None

    second = Services(root, Path("schemas"))
    second.initialize()
    assert second.worker_nodes.get(worker.id) is not None
    restarted_job_service = WorkerJobService(second, lease_seconds=-1)
    reclaimed = restarted_job_service.reclaim_expired()
    assert len(reclaimed) == 1
    assert reclaimed[0].status == WorkerJobStatus.PENDING


def test_migrations_apply_worker_tables_on_a_fresh_database(tmp_path: Path) -> None:
    database = Database(tmp_path / "adforge.sqlite3")
    database.migrate()
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    names = {row["name"] for row in rows}
    expected = {
        "worker_nodes",
        "worker_tokens",
        "worker_jobs",
        "worker_job_attempts",
        "worker_artifacts",
    }
    assert expected <= names


@pytest.fixture
def web_client(tmp_path: Path) -> TestClient:
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
    return TestClient(app)


def login(client: TestClient) -> str:
    response = client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert response.status_code == 303
    page = client.get("/")
    match = re.search(r'name="csrf" value="([^"]+)"', page.text)
    assert match is not None
    return match.group(1)


def test_worker_settings_ui_issues_a_one_time_token_and_hides_it_after(
    web_client: TestClient,
) -> None:
    csrf = login(web_client)
    response = web_client.post(
        "/settings/workers",
        data={"csrf": csrf, "name": "laptop", "capabilities": "synthetic_echo"},
    )
    assert response.status_code == 200
    match = re.search(r"<code>([^<]+)</code>", response.text)
    assert match is not None
    token = match.group(1)
    assert "." in token
    listing = web_client.get("/settings/workers")
    assert token not in listing.text


def test_worker_api_full_http_round_trip_is_outbound_only_and_bearer_authenticated(
    web_client: TestClient,
) -> None:
    csrf = login(web_client)
    context: WebContext = web_client.app.state.context
    campaign = waiting_campaign(context.services)
    context.worker_jobs.create_job(campaign.id, "synthetic_echo", {"x": 1}, "job-http-1")

    created = web_client.post(
        "/settings/workers",
        data={"csrf": csrf, "name": "http-laptop", "capabilities": "synthetic_echo"},
    )
    token = re.search(r"<code>([^<]+)</code>", created.text).group(1)  # type: ignore[union-attr]

    unauthenticated = web_client.post("/api/worker/jobs/claim")
    assert unauthenticated.status_code == 401

    headers = {"authorization": f"Bearer {token}"}
    heartbeat = web_client.post(
        "/api/worker/heartbeat",
        headers=headers,
        json={
            "agent_version": "0.1.0",
            "os": "Windows",
            "architecture": "x86_64",
            "capabilities": ["synthetic_echo"],
            "metadata": {},
        },
    )
    assert heartbeat.status_code == 200
    assert heartbeat.json()["status"] == "ONLINE"

    claim = web_client.post("/api/worker/jobs/claim", headers=headers)
    job = claim.json()["job"]
    assert job is not None

    lease = web_client.post(f"/api/worker/jobs/{job['id']}/lease", headers=headers)
    assert lease.status_code == 200

    checksum = hashlib.sha256(b"artifact-bytes").hexdigest()
    upload = web_client.post(
        f"/api/worker/jobs/{job['id']}/artifacts",
        headers=headers,
        data={"checksum": checksum},
        files={"file": ("result.bin", b"artifact-bytes", "application/octet-stream")},
    )
    assert upload.status_code == 200

    complete = web_client.post(f"/api/worker/jobs/{job['id']}/complete", headers=headers)
    assert complete.status_code == 200
    assert complete.json()["status"] == "COMPLETE"

    resumed = context.services.campaigns.get(campaign.id)
    assert resumed is not None
    assert resumed.state == CampaignState.CREATED


def _apk_job(services: Services, campaign: Campaign) -> tuple[bytes, str]:
    workspace = services.storage.campaign_workspace(campaign.id)
    apk_bytes = b"fixture-apk-bytes-not-a-real-android-package"
    apk_path = workspace / "app-capture" / "source.apk"
    apk_path.write_bytes(apk_bytes)
    checksum = hashlib.sha256(apk_bytes).hexdigest()
    job_service = WorkerJobService(services)
    job_service.create_job(
        campaign.id,
        "android_capture",
        {
            "package_id": "com.fixture.demo",
            "apk_relative_path": "app-capture/source.apk",
            "apk_filename": "source.apk",
            "apk_sha256": checksum,
        },
        "android-job-1",
    )
    return apk_bytes, checksum


def test_worker_can_download_a_declared_job_input(services: Services) -> None:
    campaign = waiting_campaign(services)
    apk_bytes, _ = _apk_job(services, campaign)
    job_service = WorkerJobService(services)
    worker, _ = make_worker(services, capabilities=["android_capture"])
    claimed = job_service.claim(worker)
    assert claimed is not None
    resolved = job_service.resolve_input(worker, claimed.id, "source.apk")
    assert resolved.read_bytes() == apk_bytes


def test_worker_cannot_download_an_undeclared_or_unleased_input(services: Services) -> None:
    campaign = waiting_campaign(services)
    _apk_job(services, campaign)
    job_service = WorkerJobService(services)
    owner, _ = make_worker(services, "owner", capabilities=["android_capture"])
    intruder, _ = make_worker(services, "intruder", capabilities=["android_capture"])
    claimed = job_service.claim(owner)
    assert claimed is not None
    with pytest.raises(WorkerNotFoundError):
        job_service.resolve_input(owner, claimed.id, "not-declared.apk")
    with pytest.raises(WorkerConflictError):
        job_service.resolve_input(intruder, claimed.id, "source.apk")
    with pytest.raises(WorkerNotFoundError):
        job_service.resolve_input(owner, claimed.id, "..")


def test_worker_api_serves_declared_job_input_over_http(web_client: TestClient) -> None:
    csrf = login(web_client)
    context: WebContext = web_client.app.state.context
    campaign = waiting_campaign(context.services)
    apk_bytes, _ = _apk_job(context.services, campaign)

    created = web_client.post(
        "/settings/workers",
        data={"csrf": csrf, "name": "android-laptop", "capabilities": "android_capture"},
    )
    token = re.search(r"<code>([^<]+)</code>", created.text).group(1)  # type: ignore[union-attr]
    headers = {"authorization": f"Bearer {token}"}

    web_client.post("/api/worker/heartbeat", headers=headers, json={
        "agent_version": "0.2.0",
        "os": "Windows",
        "architecture": "AMD64",
        "capabilities": ["android_capture"],
        "metadata": {},
    })
    claim = web_client.post("/api/worker/jobs/claim", headers=headers)
    job = claim.json()["job"]
    assert job is not None

    download = web_client.get(f"/api/worker/jobs/{job['id']}/inputs/source.apk", headers=headers)
    assert download.status_code == 200
    assert download.content == apk_bytes

    missing = web_client.get(f"/api/worker/jobs/{job['id']}/inputs/nope.apk", headers=headers)
    assert missing.status_code == 404

    traversal = web_client.get(
        f"/api/worker/jobs/{job['id']}/inputs/..%2f..%2fetc%2fpasswd", headers=headers
    )
    assert traversal.status_code in (404, 422)
