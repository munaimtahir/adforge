"""Distributed worker protocol: registration, heartbeat, leased jobs, artifacts.

Workers (external machines such as an Android-capable Windows laptop) initiate
outbound HTTPS calls to this router. AdForge never opens an inbound connection to a
worker. Authentication is a per-worker bearer token (`worker_auth.py`), entirely
separate from the owner's browser session.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from adforge.models import (
    Campaign,
    CampaignState,
    LedgerEvent,
    WorkerArtifact,
    WorkerErrorClass,
    WorkerJob,
    WorkerJobAttempt,
    WorkerJobStatus,
    WorkerNode,
    WorkerStatus,
    utc_now,
)
from adforge.orchestrator import Orchestrator, TransitionError
from adforge.security import redact_text
from adforge.services import Services
from adforge.storage import UnsafePathError, safe_component, sha256_file
from adforge.worker_auth import WorkerAuthError, authenticate

CAPABILITY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_ARTIFACT_BYTES = 500 * 1024 * 1024

DEFAULT_LEASE_SECONDS = 120
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 90


class WorkerProtocolError(RuntimeError):
    pass


class WorkerConflictError(WorkerProtocolError):
    pass


class WorkerNotFoundError(WorkerProtocolError):
    pass


def _validate_capabilities(capabilities: list[str]) -> list[str]:
    for capability in capabilities:
        if not CAPABILITY_PATTERN.fullmatch(capability):
            raise WorkerProtocolError(f"invalid capability name: {capability!r}")
    return capabilities


class WorkerJobService:
    """Durable leasing, capability matching, and recovery for distributed jobs."""

    def __init__(
        self,
        services: Services,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        heartbeat_timeout_seconds: int = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
        artifact_importers: dict[str, Callable[[Services, Campaign, WorkerJob], None]]
        | None = None,
        on_campaign_resumed: Callable[[str], Any] | None = None,
    ) -> None:
        self.services = services
        self.lease_seconds = lease_seconds
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self.orchestrator = Orchestrator(services)
        self.artifact_importers = artifact_importers or {}
        self.on_campaign_resumed = on_campaign_resumed

    def heartbeat(
        self,
        worker: WorkerNode,
        *,
        capabilities: list[str] | None,
        metadata: dict[str, Any] | None,
    ) -> WorkerNode:
        update: dict[str, Any] = {"status": WorkerStatus.ONLINE, "last_heartbeat_at": utc_now()}
        if capabilities is not None:
            update["capabilities"] = _validate_capabilities(capabilities)
        if metadata is not None:
            update["metadata"] = metadata
        saved = self.services.worker_nodes.save(worker.model_copy(update=update))
        self.sweep_offline()
        return saved

    def sweep_offline(self) -> None:
        threshold = utc_now() - timedelta(seconds=self.heartbeat_timeout_seconds)
        for node in self.services.worker_nodes.list():
            if node.status != WorkerStatus.ONLINE:
                continue
            if node.last_heartbeat_at is None or node.last_heartbeat_at < threshold:
                offline = node.model_copy(update={"status": WorkerStatus.OFFLINE})
                self.services.worker_nodes.save(offline)

    def create_job(
        self,
        campaign_id: str,
        capability: str,
        payload: dict[str, Any],
        idempotency_key: str,
        *,
        task_id: str | None = None,
    ) -> WorkerJob:
        existing = [
            job
            for job in self.services.worker_jobs.find_by("idempotency_key", idempotency_key)
            if job.campaign_id == campaign_id
        ]
        if existing:
            return existing[0]
        return self.services.worker_jobs.save(
            WorkerJob(
                campaign_id=campaign_id,
                task_id=task_id,
                capability=capability,
                payload=payload,
                idempotency_key=idempotency_key,
            )
        )

    def claim(self, worker: WorkerNode) -> WorkerJob | None:
        self.reclaim_expired()
        with self.services.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT id, payload_json FROM worker_jobs ORDER BY created_at"
            ).fetchall()
            candidate = None
            for row in rows:
                job = WorkerJob.model_validate_json(row["payload_json"])
                if job.status == WorkerJobStatus.PENDING and job.capability in worker.capabilities:
                    candidate = job
                    break
            if candidate is None:
                return None
            claimed = candidate.model_copy(
                update={
                    "status": WorkerJobStatus.CLAIMED,
                    "worker_id": worker.id,
                    "lease_expires_at": utc_now() + timedelta(seconds=self.lease_seconds),
                    "attempt": candidate.attempt + 1,
                }
            )
            connection.execute(
                "UPDATE worker_jobs SET payload_json=?, updated_at=? WHERE id=?",
                (claimed.model_dump_json(), claimed.updated_at.isoformat(), claimed.id),
            )
        self.services.worker_nodes.save(worker.model_copy(update={"active_job_id": claimed.id}))
        self._append_attempt(claimed, worker, WorkerJobStatus.CLAIMED)
        self._ledger(claimed, "worker_job_claimed", "CLAIMED", worker=worker)
        return claimed

    def claim_specific(self, worker: WorkerNode, job_id: str) -> WorkerJob:
        """Claim one exact job by id, for a human manually fulfilling it via the web
        UI (`web.py`'s manual worker-job completion route) rather than a real
        worker's `claim()` scan for the oldest matching `PENDING` job. Otherwise
        identical bookkeeping to `claim()`.
        """
        with self.services.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM worker_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise WorkerNotFoundError(f"worker job not found: {job_id}")
            candidate = WorkerJob.model_validate_json(row["payload_json"])
            if candidate.status != WorkerJobStatus.PENDING:
                raise WorkerConflictError(f"job is not PENDING: {candidate.status}")
            claimed = candidate.model_copy(
                update={
                    "status": WorkerJobStatus.CLAIMED,
                    "worker_id": worker.id,
                    "lease_expires_at": utc_now() + timedelta(seconds=self.lease_seconds),
                    "attempt": candidate.attempt + 1,
                }
            )
            connection.execute(
                "UPDATE worker_jobs SET payload_json=?, updated_at=? WHERE id=?",
                (claimed.model_dump_json(), claimed.updated_at.isoformat(), claimed.id),
            )
        self.services.worker_nodes.save(worker.model_copy(update={"active_job_id": claimed.id}))
        self._append_attempt(claimed, worker, WorkerJobStatus.CLAIMED)
        self._ledger(claimed, "worker_job_claimed", "CLAIMED", worker=worker)
        return claimed

    def renew_lease(self, worker: WorkerNode, job_id: str) -> WorkerJob:
        job = self._owned_job(worker, job_id, {WorkerJobStatus.CLAIMED, WorkerJobStatus.RUNNING})
        renewed = job.model_copy(
            update={
                "status": WorkerJobStatus.RUNNING,
                "lease_expires_at": utc_now() + timedelta(seconds=self.lease_seconds),
            }
        )
        return self.services.worker_jobs.save(renewed)

    def record_progress(self, worker: WorkerNode, job_id: str, detail: str) -> WorkerJob:
        job = self._owned_job(worker, job_id, {WorkerJobStatus.CLAIMED, WorkerJobStatus.RUNNING})
        self._ledger(job, "worker_job_progress", "RUNNING", worker=worker, detail=detail)
        return job

    def resolve_input(self, worker: WorkerNode, job_id: str, filename: str) -> Path:
        """Resolve a job input file (e.g. the APK to install) for the leased worker.

        Only a filename the job's own payload declared (`apk_filename`, matched
        against `apk_relative_path`) may be fetched — a worker cannot use this to
        read arbitrary campaign workspace files, and a worker without the lease on
        this job cannot read it at all.
        """
        job = self._owned_job(worker, job_id, {WorkerJobStatus.CLAIMED, WorkerJobStatus.RUNNING})
        relative_path = job.payload.get("apk_relative_path")
        expected_filename = job.payload.get("apk_filename")
        try:
            safe_name = safe_component(filename)
        except UnsafePathError as exc:
            raise WorkerNotFoundError("job declares no such input") from exc
        if not relative_path or not expected_filename or safe_name != expected_filename:
            raise WorkerNotFoundError("job declares no such input")
        try:
            resolved = self.services.storage.campaign_path(
                job.campaign_id, *Path(relative_path).parts
            )
        except UnsafePathError as exc:
            raise WorkerNotFoundError("job input path is invalid") from exc
        if not resolved.is_file():
            raise WorkerNotFoundError("job input file does not exist")
        return resolved

    def store_artifact(
        self,
        worker: WorkerNode,
        job_id: str,
        *,
        filename: str,
        content: bytes,
        content_type: str,
        declared_checksum: str,
    ) -> WorkerArtifact:
        job = self._owned_job(worker, job_id, {WorkerJobStatus.CLAIMED, WorkerJobStatus.RUNNING})
        if len(content) > MAX_ARTIFACT_BYTES:
            raise WorkerProtocolError("artifact exceeds the maximum allowed size")
        safe_name = safe_component(filename)
        workspace = self.services.storage.campaign_workspace(job.campaign_id)
        artifact_dir = workspace / "worker-artifacts" / safe_component(job.id)
        artifact_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = artifact_dir / safe_name
        target.write_bytes(content)
        actual_checksum = sha256_file(target)
        if actual_checksum != declared_checksum:
            target.unlink(missing_ok=True)
            raise WorkerProtocolError("artifact checksum does not match declared value")
        artifact = self.services.worker_artifacts.save(
            WorkerArtifact(
                job_id=job.id,
                filename=safe_name,
                checksum=actual_checksum,
                content_type=content_type,
                size_bytes=len(content),
                filepath=str(target.relative_to(workspace)),
            )
        )
        self._ledger(job, "worker_artifact_uploaded", "COMPLETE", worker=worker, detail=safe_name)
        return artifact

    def complete(self, worker: WorkerNode, job_id: str) -> WorkerJob:
        job = self._worker_job(job_id)
        if job.worker_id != worker.id:
            raise WorkerConflictError("job is not leased to this worker")
        if job.status == WorkerJobStatus.COMPLETE:
            return job
        if job.status not in {WorkerJobStatus.CLAIMED, WorkerJobStatus.RUNNING}:
            raise WorkerConflictError(f"job cannot complete from status {job.status}")
        completed = self.services.worker_jobs.save(
            job.model_copy(update={"status": WorkerJobStatus.COMPLETE, "lease_expires_at": None})
        )
        self.services.worker_nodes.save(worker.model_copy(update={"active_job_id": None}))
        self._append_attempt(completed, worker, WorkerJobStatus.COMPLETE)
        self._ledger(completed, "worker_job_completed", "COMPLETE", worker=worker)
        self._resume_campaign_if_waiting(completed)
        return completed

    def fail(
        self,
        worker: WorkerNode,
        job_id: str,
        *,
        error_class: WorkerErrorClass,
        detail: str,
    ) -> WorkerJob:
        job = self._owned_job(worker, job_id, {WorkerJobStatus.CLAIMED, WorkerJobStatus.RUNNING})
        self._append_attempt(
            job, worker, WorkerJobStatus.FAILED, error_class=error_class, detail=detail
        )
        self.services.worker_nodes.save(worker.model_copy(update={"active_job_id": None}))
        retryable = error_class == WorkerErrorClass.RETRYABLE and job.attempt < job.max_attempts
        if retryable:
            requeued = self.services.worker_jobs.save(
                job.model_copy(
                    update={
                        "status": WorkerJobStatus.PENDING,
                        "worker_id": None,
                        "lease_expires_at": None,
                        "failure_summary": redact_text(detail),
                    }
                )
            )
            self._ledger(requeued, "worker_job_requeued", "PENDING", worker=worker, detail=detail)
            return requeued
        failed = self.services.worker_jobs.save(
            job.model_copy(
                update={
                    "status": WorkerJobStatus.FAILED,
                    "lease_expires_at": None,
                    "failure_summary": redact_text(detail),
                }
            )
        )
        self._ledger(failed, "worker_job_failed", "FAILED", worker=worker, detail=detail)
        campaign = self.services.campaigns.get(failed.campaign_id)
        if campaign is not None and campaign.state not in (
            CampaignState.BLOCKED,
            CampaignState.COMPLETE,
        ):
            try:
                self.orchestrator.transition(campaign.id, CampaignState.BLOCKED)
            except TransitionError:
                pass
        return failed

    def reclaim_expired(self) -> list[WorkerJob]:
        now = utc_now()
        reclaimed: list[WorkerJob] = []
        for job in self.services.worker_jobs.list():
            if job.status not in {WorkerJobStatus.CLAIMED, WorkerJobStatus.RUNNING}:
                continue
            if job.lease_expires_at is None or job.lease_expires_at >= now:
                continue
            if job.attempt >= job.max_attempts:
                expired = job.model_copy(
                    update={
                        "status": WorkerJobStatus.FAILED,
                        "lease_expires_at": None,
                        "failure_summary": "lease expired after exhausting attempt budget",
                    }
                )
                self._ledger(expired, "worker_job_lease_expired", "FAILED")
            else:
                expired = job.model_copy(
                    update={
                        "status": WorkerJobStatus.PENDING,
                        "worker_id": None,
                        "lease_expires_at": None,
                    }
                )
                self._ledger(expired, "worker_job_lease_expired", "PENDING")
            self.services.worker_jobs.save(expired)
            reclaimed.append(expired)
        return reclaimed

    def _owned_job(
        self, worker: WorkerNode, job_id: str, allowed: set[WorkerJobStatus]
    ) -> WorkerJob:
        job = self._worker_job(job_id)
        if job.worker_id != worker.id:
            raise WorkerConflictError("job is not leased to this worker")
        if job.status not in allowed:
            raise WorkerConflictError(f"job is not in an operable status: {job.status}")
        return job

    def _worker_job(self, job_id: str) -> WorkerJob:
        job = self.services.worker_jobs.get(job_id)
        if job is None:
            raise WorkerNotFoundError(f"worker job not found: {job_id}")
        return job

    def _resume_campaign_if_waiting(self, job: WorkerJob) -> None:
        campaign = self.services.campaigns.get(job.campaign_id)
        if campaign is None or campaign.state != CampaignState.WAITING_FOR_WORKER:
            return
        importer = self.artifact_importers.get(job.capability)
        if importer is not None:
            try:
                importer(self.services, campaign, job)
            except Exception as exc:  # noqa: BLE001 - a bad import must not resume/crash
                self._ledger(
                    job,
                    "worker_artifact_import_failed",
                    "WAITING_FOR_WORKER",
                    detail=f"{type(exc).__name__}: {exc}",
                )
                return
        resumed = self.orchestrator.resume(campaign.id)
        if self.on_campaign_resumed is not None:
            self.on_campaign_resumed(resumed.id)

    def _append_attempt(
        self,
        job: WorkerJob,
        worker: WorkerNode,
        status_value: WorkerJobStatus,
        *,
        error_class: WorkerErrorClass | None = None,
        detail: str | None = None,
    ) -> None:
        self.services.worker_job_attempts.save(
            WorkerJobAttempt(
                job_id=job.id,
                worker_id=worker.id,
                attempt=max(job.attempt, 1),
                status=status_value,
                error_class=error_class,
                detail=redact_text(detail) if detail else None,
            )
        )

    def _ledger(
        self,
        job: WorkerJob,
        event_type: str,
        status_value: str,
        *,
        worker: WorkerNode | None = None,
        detail: str | None = None,
    ) -> None:
        self.services.ledger.append(
            LedgerEvent(
                campaign_id=job.campaign_id,
                stage="WORKER_JOB",
                event_type=event_type,
                status=status_value,
                details={
                    "job_id": job.id,
                    "capability": job.capability,
                    "worker_id": worker.id if worker else job.worker_id,
                    "detail": redact_text(detail) if detail else None,
                },
            )
        )


class HeartbeatRequest(BaseModel):
    agent_version: str = Field(min_length=1)
    os: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ClaimResponse(BaseModel):
    job: WorkerJob | None


class FailRequest(BaseModel):
    error_class: WorkerErrorClass
    detail: str = Field(min_length=1, max_length=4000)


class ProgressRequest(BaseModel):
    detail: str = Field(min_length=1, max_length=4000)


def current_worker(request: Request) -> WorkerNode:
    """Resolve the calling worker from its bearer token.

    Defined at module scope (not nested in `build_worker_router`) because FastAPI
    resolves string annotations (`from __future__ import annotations`) against a
    function's module globals — a name only reachable via an enclosing function's
    local scope cannot be resolved and raises a `ForwardRef` error. `services` is
    read from `request.app.state.worker_services`, set by `build_worker_router`.
    """
    services: Services = request.app.state.worker_services
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else None
    try:
        return authenticate(services, token)
    except WorkerAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


Worker = Annotated[WorkerNode, Depends(current_worker)]


def build_worker_router(services: Services, job_service: WorkerJobService) -> APIRouter:
    router = APIRouter(prefix="/api/worker", tags=["worker"])

    @router.post("/heartbeat")
    def heartbeat(worker: Worker, body: HeartbeatRequest) -> WorkerNode:
        try:
            updated = worker.model_copy(
                update={
                    "agent_version": body.agent_version,
                    "os": body.os,
                    "architecture": body.architecture,
                }
            )
            services.worker_nodes.save(updated)
            return job_service.heartbeat(
                updated, capabilities=body.capabilities, metadata=body.metadata
            )
        except WorkerProtocolError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    @router.post("/jobs/claim", response_model=ClaimResponse)
    def claim(worker: Worker) -> ClaimResponse:
        return ClaimResponse(job=job_service.claim(worker))

    @router.post("/jobs/{job_id}/lease")
    def renew(worker: Worker, job_id: str) -> WorkerJob:
        try:
            return job_service.renew_lease(worker, job_id)
        except WorkerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkerConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/jobs/{job_id}/inputs/{filename}")
    def download_input(worker: Worker, job_id: str, filename: str) -> FileResponse:
        try:
            resolved = job_service.resolve_input(worker, job_id, filename)
        except WorkerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkerConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return FileResponse(resolved, filename=resolved.name, media_type="application/octet-stream")

    @router.post("/jobs/{job_id}/progress")
    def progress(worker: Worker, job_id: str, body: ProgressRequest) -> WorkerJob:
        try:
            return job_service.record_progress(worker, job_id, body.detail)
        except WorkerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkerConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/jobs/{job_id}/artifacts")
    async def upload_artifact(
        worker: Worker,
        job_id: str,
        checksum: Annotated[str, Form()],
        file: Annotated[UploadFile, File()],
    ) -> WorkerArtifact:
        content = await file.read()
        try:
            return job_service.store_artifact(
                worker,
                job_id,
                filename=file.filename or "artifact.bin",
                content=content,
                content_type=file.content_type or "application/octet-stream",
                declared_checksum=checksum,
            )
        except WorkerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkerConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (WorkerProtocolError, UnsafePathError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/jobs/{job_id}/complete")
    def complete(worker: Worker, job_id: str) -> WorkerJob:
        try:
            return job_service.complete(worker, job_id)
        except WorkerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkerConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/jobs/{job_id}/fail")
    def fail(worker: Worker, job_id: str, body: FailRequest) -> WorkerJob:
        try:
            return job_service.fail(
                worker, job_id, error_class=body.error_class, detail=body.detail
            )
        except WorkerNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkerConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router
