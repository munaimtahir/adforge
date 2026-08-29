"""Structured, truth-gated logical AI tasks for creative production."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from adforge.creative_quality import CreativeStrategy2, ScriptPlan2, Storyboard2
from adforge.models import Campaign, LedgerEvent, ProductTruthSnapshot
from adforge.product_truth import ProductTruthService
from adforge.providers import ProviderExecutor, ProviderRequest, ReasoningProvider
from adforge.services import Services


class CreativeOutputError(ValueError):
    pass


class StructuredOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CampaignDirection(StructuredOutput):
    objective: str
    audience: str
    tone: str
    success_criteria: list[str]
    claims: list[str] = Field(default_factory=list)


class CreativeStrategy(StructuredOutput):
    hook: str
    positioning: str
    narrative: list[str]
    claims: list[str] = Field(default_factory=list)


class TimedLine(StructuredOutput):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    text: str
    mode: str = Field(pattern=r"^(NARRATION|ON_SCREEN|CTA)$")
    claim: str | None = None

    @model_validator(mode="after")
    def valid_interval(self) -> TimedLine:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("timed line end must be after start")
        return self


class ScriptOutput(StructuredOutput):
    target_duration_seconds: float = Field(gt=0)
    lines: list[TimedLine] = Field(min_length=1)


class StoryboardScene(StructuredOutput):
    scene_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    description: str
    framing: str
    required_asset_ids: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_interval(self) -> StoryboardScene:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("scene end must be after start")
        return self


class StoryboardOutput(StructuredOutput):
    target_duration_seconds: float = Field(gt=0)
    scenes: list[StoryboardScene] = Field(min_length=1)


class ContinuityOutput(StructuredOutput):
    visual_rules: list[str]
    characters: list[dict[str, str]] = Field(default_factory=list)
    environments: list[dict[str, str]] = Field(default_factory=list)
    product_ui_rules: list[str] = Field(default_factory=list)


class AssetClassification(StrEnum):
    REUSE = "REUSE"
    GENERATE_IMAGE = "GENERATE_IMAGE"
    GENERATE_VIDEO = "GENERATE_VIDEO"
    CAPTURE_APP = "CAPTURE_APP"
    GENERATE_AUDIO = "GENERATE_AUDIO"
    RENDER_GRAPHIC = "RENDER_GRAPHIC"


class AssetNeed(StructuredOutput):
    asset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    scene_ids: list[str] = Field(min_length=1)
    classification: AssetClassification
    description: str
    deterministic: bool = False
    dependencies: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def deterministic_work_is_not_video_generation(self) -> AssetNeed:
        if self.deterministic and self.classification == AssetClassification.GENERATE_VIDEO:
            raise ValueError("deterministic asset cannot use generative video")
        return self


class AssetPlanOutput(StructuredOutput):
    assets: list[AssetNeed] = Field(min_length=1)

    @model_validator(mode="after")
    def dependencies_exist(self) -> AssetPlanOutput:
        identifiers = {asset.asset_id for asset in self.assets}
        if len(identifiers) != len(self.assets):
            raise ValueError("asset IDs must be unique")
        missing = {
            dependency
            for asset in self.assets
            for dependency in asset.dependencies
            if dependency not in identifiers
        }
        if missing:
            raise ValueError("unresolved asset dependencies: " + ", ".join(sorted(missing)))
        return self


class GenerationPromptOutput(StructuredOutput):
    scene_id: str
    prompt: str
    negative_constraints: list[str]
    reference_asset_ids: list[str] = Field(default_factory=list)
    duration_seconds: float = Field(gt=0)
    aspect_ratio: str = Field(pattern=r"^(9:16|16:9|1:1)$")


class EditDirectionOutput(StructuredOutput):
    pacing: str
    transitions: list[str]
    deterministic_text: list[str]
    audio_notes: list[str]
    claims: list[str] = Field(default_factory=list)


class ProductTruthQCOutput(StructuredOutput):
    claims_checked: list[str]
    passed: bool
    violations: list[str] = Field(default_factory=list)


ROLE_MODELS: dict[str, type[BaseModel]] = {
    "campaign-director": CampaignDirection,
    "creative-strategy": CreativeStrategy,
    "script": ScriptOutput,
    "storyboard": StoryboardOutput,
    "continuity": ContinuityOutput,
    "asset-plan": AssetPlanOutput,
    "generation-prompt": GenerationPromptOutput,
    "edit-director": EditDirectionOutput,
    "product-truth-qc": ProductTruthQCOutput,
    # Creative Quality 2.0 -- the real, production-wired strategy/script/storyboard
    # roles. Kept in the same generic role registry/dispatch as V1 so every existing
    # AI-provider, persistence, versioning, and Product Truth claim-gate mechanism in
    # `CreativePipeline` applies unchanged; only the output contract is typed CQ2.
    "creative-strategy-v2": CreativeStrategy2,
    "script-v2": ScriptPlan2,
    "storyboard-v2": Storyboard2,
}

ROLE_DIRECTORIES = {
    "campaign-director": "strategy",
    "creative-strategy": "strategy",
    "script": "script",
    "storyboard": "storyboard",
    "continuity": "storyboard",
    "asset-plan": "asset-plan",
    "generation-prompt": "asset-plan",
    "edit-director": "edit",
    "product-truth-qc": "qc",
    "creative-strategy-v2": "strategy",
    "script-v2": "script",
    "storyboard-v2": "storyboard",
}

ROLE_INSTRUCTIONS = {
    "campaign-director": "Convert the campaign brief into a production direction.",
    "creative-strategy": "Create a truthful hook, positioning, and narrative.",
    "script": "Write a timed narration, on-screen copy, and CTA script.",
    "storyboard": "Create contiguous scenes that exactly fill the target duration.",
    "continuity": "Define reusable visual, character, environment, and app UI rules.",
    "asset-plan": "Classify every asset using the allowed production classifications.",
    "generation-prompt": "Write one provider-ready media prompt with constraints.",
    "edit-director": "Specify deterministic pacing, transitions, text, and audio intent.",
    "product-truth-qc": "Check every supplied claim against Product Truth.",
    "creative-strategy-v2": (
        "Produce a full Creative Quality 2.0 advertising strategy: audience insight and "
        "tension, objective, single-minded proposition, benefit, reason to believe, hook, "
        "visual thesis, demonstration objective, proof moments, CTA, brand personality, "
        "pace/energy, a recommended shot count, the generated-vs-real footage balance, "
        "raw-UI tolerance, avoidances, claim boundaries, audio direction, typography "
        "direction, and visual continuity direction."
    ),
    "script-v2": (
        "Write a Creative Quality 2.0 script as timed beats across distinct channels "
        "(NARRATION, OVERLAY, PRODUCT_UI, SOUND_DESIGN, SILENCE, CTA). Never repeat the "
        "same wording across narration/CTA and overlay -- each channel must say something "
        "the others do not."
    ),
    "storyboard-v2": (
        "Create a Creative Quality 2.0 storyboard: contiguous shots with unique canonical "
        "shot_id/scene_id, explicit visual source, purpose, composition intent, typography "
        "intent, transitions, capture instruction (for ANDROID_DIRECT_CAPTURE/"
        "ANDROID_DEVICE_COMPOSITE shots), and keyboard policy."
    ),
}


def latest_role_output(services: Services, campaign_id: str, role: str) -> BaseModel | None:
    """Shared by `campaign_stages.py` and `qc.py`: load the latest persisted
    output for a pipeline role, whether a V1 or Creative Quality 2.0 role.
    """
    workspace = services.storage.campaign_workspace(campaign_id)
    directory = workspace / ROLE_DIRECTORIES[role]
    versions = sorted(directory.glob(f"{role}.v*.json"))
    if not versions:
        return None
    payload = json.loads(versions[-1].read_text())
    return ROLE_MODELS[role].model_validate(payload["output"])


class CreativePipeline:
    def __init__(self, services: Services) -> None:
        self.services = services
        self.truth_service = ProductTruthService(services)

    def build_request(
        self,
        role: str,
        campaign: Campaign,
        snapshot: ProductTruthSnapshot,
        *,
        target_duration_seconds: float = 20,
        additional_context: dict[str, Any] | None = None,
    ) -> ProviderRequest:
        model = self._model(role)
        context = self._context(role, campaign, snapshot, target_duration_seconds)
        if additional_context:
            context["task_inputs"] = additional_context
        return ProviderRequest(
            request_id=f"{campaign.id}-{role}",
            task_type=role,
            capability="product_truth_qc" if role == "product-truth-qc" else "creative",
            prompt=ROLE_INSTRUCTIONS[role],
            context=context,
            output_schema=model.model_json_schema(),
        )

    def execute(
        self,
        role: str,
        campaign: Campaign,
        snapshot: ProductTruthSnapshot,
        task_id: str,
        provider: ReasoningProvider,
        *,
        target_duration_seconds: float = 20,
        additional_context: dict[str, Any] | None = None,
    ) -> BaseModel:
        request = self.build_request(
            role,
            campaign,
            snapshot,
            target_duration_seconds=target_duration_seconds,
            additional_context=additional_context,
        )
        response = ProviderExecutor(self.services).execute(
            campaign.id, task_id, provider, request
        )
        return self.persist(role, campaign, snapshot, response.output)

    def persist(
        self,
        role: str,
        campaign: Campaign,
        snapshot: ProductTruthSnapshot,
        output: dict[str, Any],
    ) -> BaseModel:
        parsed = self._model(role).model_validate(output)
        self._validate_claims(parsed, snapshot)
        self._validate_timing(parsed)
        workspace = self.services.storage.campaign_workspace(campaign.id)
        directory = workspace / ROLE_DIRECTORIES[role]
        version = len(list(directory.glob(f"{role}.v*.json"))) + 1
        path = directory / f"{role}.v{version}.json"
        payload = {
            "role": role,
            "version": version,
            "product_truth_snapshot_id": snapshot.id,
            "product_truth_checksum": snapshot.checksum,
            "output": parsed.model_dump(mode="json"),
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        self.services.ledger.append(
            LedgerEvent(
                campaign_id=campaign.id,
                stage=role.upper().replace("-", "_"),
                event_type="creative_output_versioned",
                status="COMPLETE",
                details={
                    "role": role,
                    "version": version,
                    "path": str(path.relative_to(workspace)),
                    "product_truth_snapshot_id": snapshot.id,
                },
            )
        )
        return parsed

    def _model(self, role: str) -> type[BaseModel]:
        try:
            return ROLE_MODELS[role]
        except KeyError as exc:
            raise CreativeOutputError(f"unknown creative role: {role}") from exc

    @staticmethod
    def _context(
        role: str,
        campaign: Campaign,
        snapshot: ProductTruthSnapshot,
        target_duration_seconds: float,
    ) -> dict[str, Any]:
        truth = snapshot.truth
        base: dict[str, Any] = {
            "campaign_brief": campaign.brief,
            "product_truth_snapshot_id": snapshot.id,
            "product_truth_checksum": snapshot.checksum,
            "approved_features": truth["approved_features"],
            "prohibited_claims": truth["prohibited_claims"],
        }
        if role in {"campaign-director", "creative-strategy", "script", "storyboard"}:
            base["audiences"] = truth.get("audiences", [])
            base["known_limitations"] = truth.get("known_limitations", [])
        if role in {"script", "storyboard", "asset-plan", "edit-director"}:
            base["target_duration_seconds"] = target_duration_seconds
        if role in {"asset-plan", "product-truth-qc"}:
            base["demo_workflows"] = truth.get("demo_workflows", [])
        if role in {"creative-strategy-v2", "script-v2", "storyboard-v2"}:
            base["campaign_objective"] = campaign.brief
            base["audiences"] = truth.get("audiences", [])
            base["known_limitations"] = truth.get("known_limitations", [])
            base["target_duration_seconds"] = target_duration_seconds
            base["format"] = {
                "aspect_ratio": "9:16",
                "target_duration_seconds": target_duration_seconds,
            }
            base["available_product_assets"] = truth.get("demo_workflows", [])
            base["creative_constraints"] = {
                "prohibited_claims": truth["prohibited_claims"],
                "known_limitations": truth.get("known_limitations", []),
                "privacy_claims": truth.get("privacy_claims", []),
            }
        return base

    def _validate_claims(self, output: BaseModel, snapshot: ProductTruthSnapshot) -> None:
        claims: list[str] = []
        if isinstance(output, (CampaignDirection, CreativeStrategy, EditDirectionOutput)):
            claims.extend(output.claims)
        if isinstance(output, ScriptOutput):
            claims.extend(line.claim for line in output.lines if line.claim)
        if isinstance(output, StoryboardOutput):
            claims.extend(claim for scene in output.scenes for claim in scene.claims)
        if isinstance(output, ProductTruthQCOutput):
            claims.extend(output.claims_checked)
        if isinstance(output, ScriptPlan2):
            claims.extend(beat.claim for beat in output.beats if beat.claim)
        for claim in claims:
            self.truth_service.validate_claim(snapshot, claim)

    @staticmethod
    def _validate_timing(output: BaseModel) -> None:
        intervals: list[tuple[float, float]] = []
        target: float | None = None
        if isinstance(output, ScriptOutput):
            intervals = [(line.start_seconds, line.end_seconds) for line in output.lines]
            target = output.target_duration_seconds
        elif isinstance(output, StoryboardOutput):
            intervals = [(scene.start_seconds, scene.end_seconds) for scene in output.scenes]
            target = output.target_duration_seconds
        # CreativeStrategy2/ScriptPlan2/Storyboard2 already self-enforce contiguous,
        # gap-free timing via their own pydantic model_validators (see
        # `creative_quality.py`); nothing further to check here for CQ2 roles.
        if not intervals or target is None:
            return
        ordered = sorted(intervals)
        if abs(ordered[0][0]) > 0.01 or abs(ordered[-1][1] - target) > 0.01:
            raise CreativeOutputError("timings do not fill the target duration")
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if abs(previous[1] - current[0]) > 0.01:
                raise CreativeOutputError("timings contain a gap or overlap")
