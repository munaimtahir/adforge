"""Typed domain records shared across persistence and service boundaries."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TruthReadiness(StrEnum):
    UNKNOWN = "UNKNOWN"
    READY = "READY"
    BLOCKED = "BLOCKED"


class CampaignState(StrEnum):
    CREATED = "CREATED"
    PRODUCT_TRUTH_VALIDATION = "PRODUCT_TRUTH_VALIDATION"
    STRATEGY = "STRATEGY"
    SCRIPT = "SCRIPT"
    STORYBOARD = "STORYBOARD"
    ASSET_PLAN = "ASSET_PLAN"
    ASSET_GENERATION = "ASSET_GENERATION"
    APP_CAPTURE = "APP_CAPTURE"
    AUDIO_PRODUCTION = "AUDIO_PRODUCTION"
    EDIT_PLAN = "EDIT_PLAN"
    DRAFT_RENDER = "DRAFT_RENDER"
    QC = "QC"
    REPAIR = "REPAIR"
    FINAL_RENDER = "FINAL_RENDER"
    EXPORT = "EXPORT"
    COMPLETE = "COMPLETE"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    WAITING_FOR_EXTERNAL_ASSET = "WAITING_FOR_EXTERNAL_ASSET"
    WAITING_FOR_USER = "WAITING_FOR_USER"


class TaskState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class Product(Record):
    name: str = Field(min_length=1)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    truth_readiness: TruthReadiness = TruthReadiness.UNKNOWN
    truth_source_path: str | None = None


class ProductTruthSnapshot(Record):
    product_id: str
    campaign_id: str
    checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    truth: dict[str, Any]
    provenance: list[dict[str, Any]] = Field(default_factory=list)


class Campaign(Record):
    product_id: str
    name: str = Field(min_length=1)
    brief: str = Field(min_length=1)
    state: CampaignState = CampaignState.CREATED
    truth_snapshot_id: str | None = None
    active: bool = False
    resume_state: CampaignState | None = None


class CampaignTask(Record):
    campaign_id: str
    task_type: str
    state: TaskState = TaskState.PENDING
    idempotency_key: str
    dependencies: list[str] = Field(default_factory=list)
    attempt: int = Field(default=0, ge=0, le=3)
    max_attempts: int = Field(default=3, ge=1, le=3)
    output: dict[str, Any] | None = None
    failure_summary: str | None = None
    targeted_asset_ids: list[str] = Field(default_factory=list)


class Asset(Record):
    campaign_id: str
    asset_type: str
    status: str
    filepath: str
    source: str = "local"
    provider: str | None = None
    version: int = Field(default=1, ge=1)
    checksum: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    qc_score: float | None = Field(default=None, ge=0, le=1)
    used_in_final: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)


class ProviderExecution(Record):
    campaign_id: str
    task_id: str
    provider: str
    model: str | None = None
    attempt: int = Field(ge=1, le=3)
    status: str
    stdout: str = ""
    stderr: str = ""
    duration_ms: int | None = Field(default=None, ge=0)


class QCResult(Record):
    campaign_id: str
    render_id: str | None = None
    passed: bool
    blockers: list[str] = Field(default_factory=list)
    advisories: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class HandoffPackage(Record):
    campaign_id: str
    handoff_type: str
    status: str
    request_path: str
    return_path: str
    checksum: str | None = None


class LedgerEvent(Record):
    campaign_id: str
    stage: str
    event_type: str
    status: str
    task_id: str | None = None
    provider: str | None = None
    model: str | None = None
    attempt: int | None = Field(default=None, ge=1, le=3)
    input_asset_ids: list[str] = Field(default_factory=list)
    output_asset_ids: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class Render(Record):
    campaign_id: str
    status: str
    spec_path: str
    output_path: str
    aspect_ratio: str
    duration_seconds: float = Field(gt=0)
    checksum: str | None = None


class Configuration(Record):
    key: str
    value: Any
    secret: bool = False


DOMAIN_MODELS: tuple[type[Record], ...] = (
    Product,
    ProductTruthSnapshot,
    Campaign,
    CampaignTask,
    Asset,
    ProviderExecution,
    QCResult,
    HandoffPackage,
    LedgerEvent,
    Render,
    Configuration,
)
