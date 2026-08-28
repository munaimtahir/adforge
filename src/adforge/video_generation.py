"""Video generation adapter boundary, Flow browser driver, and handoff protocol."""

from __future__ import annotations

import json
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from adforge.models import (
    Asset,
    CampaignState,
    HandoffPackage,
    LedgerEvent,
)
from adforge.orchestrator import Orchestrator
from adforge.services import Services
from adforge.storage import UnsafePathError, sha256_file


class VideoGenerationError(RuntimeError):
    pass


class FlowLoginRequired(VideoGenerationError):
    pass


class GenerationBudgetError(VideoGenerationError):
    pass


class ReturnManifestError(VideoGenerationError):
    pass


class GenerationScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    prompt: str = Field(min_length=1)
    negative_constraints: list[str] = Field(default_factory=list)
    reference_paths: list[str] = Field(default_factory=list)
    aspect_ratio: str = Field(pattern=r"^(9:16|16:9|1:1)$")
    duration_seconds: float = Field(gt=0, le=30)
    generation_count: int = Field(default=1, ge=1, le=4)
    max_attempts: int = Field(default=3, ge=1, le=3)
    estimated_credits_per_attempt: float = Field(default=1, ge=0)
    expected_filename: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*\.mp4$")

    def expected_filenames(self) -> list[str]:
        path = Path(self.expected_filename)
        return [
            self.expected_filename if index == 1 else f"{path.stem}-candidate-{index}{path.suffix}"
            for index in range(1, self.generation_count + 1)
        ]


class VideoGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: str
    preferred_model: str = "Veo"
    mode: str = "text-to-video"
    credit_budget: float = Field(gt=0)
    scenes: list[GenerationScene] = Field(min_length=1)

    @model_validator(mode="after")
    def budget_covers_declared_attempts(self) -> VideoGenerationRequest:
        maximum = sum(
            scene.max_attempts * scene.generation_count * scene.estimated_credits_per_attempt
            for scene in self.scenes
        )
        if maximum > self.credit_budget:
            raise ValueError(
                f"declared generation attempts require {maximum:g} credits, "
                f"budget is {self.credit_budget:g}"
            )
        return self


class GeneratedVideo(BaseModel):
    scene_id: str
    path: Path
    provider: str
    model: str | None = None
    attempts: int = Field(ge=1, le=3)
    credits: float | None = Field(default=None, ge=0)


class VideoGenerationHealth(BaseModel):
    available: bool
    login_state: str
    detail: str | None = None


class VideoGenerationProvider(ABC):
    @abstractmethod
    def health(self) -> VideoGenerationHealth: ...

    @abstractmethod
    def generate(self, request: VideoGenerationRequest) -> list[GeneratedVideo]: ...


class FlowDriver(Protocol):
    def health(self) -> VideoGenerationHealth: ...

    def generate(
        self, scene: GenerationScene, destination: Path, preferred_model: str, mode: str
    ) -> Path: ...


class PlaywrightFlowDriver:
    """Configurable persistent-profile browser driver for the current Flow UI."""

    def __init__(
        self,
        profile_path: Path,
        *,
        flow_url: str = "https://labs.google/fx/tools/flow",
        chromium_executable: str | None = None,
        headless: bool = True,
        prompt_selector: str = "textarea",
        generate_selector: str = 'button:has-text("Generate")',
        download_selector: str = 'button:has-text("Download")',
    ) -> None:
        self.profile_path = profile_path.resolve()
        self.profile_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.profile_path, 0o700)
        self.flow_url = flow_url
        self.chromium_executable = (
            chromium_executable
            or shutil.which("chromium")
            or shutil.which("google-chrome")
            or shutil.which("google-chrome-stable")
        )
        self.headless = headless
        self.prompt_selector = prompt_selector
        self.generate_selector = generate_selector
        self.download_selector = download_selector

    def _playwright(self) -> Any:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise VideoGenerationError("Playwright is not installed") from exc
        return sync_playwright

    def health(self) -> VideoGenerationHealth:
        if self.chromium_executable is None:
            return VideoGenerationHealth(
                available=False,
                login_state="UNAVAILABLE",
                detail="Chromium executable is not configured",
            )
        sync_playwright = self._playwright()
        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(self.profile_path),
                    executable_path=self.chromium_executable,
                    headless=self.headless,
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(self.flow_url, wait_until="domcontentloaded", timeout=30_000)
                login_required = "accounts.google" in page.url or page.get_by_text(
                    "Sign in", exact=False
                ).count() > 0
                generation_ready = (
                    page.locator(self.prompt_selector).count() > 0
                    and page.locator(self.generate_selector).count() > 0
                )
                context.close()
        except Exception as exc:
            return VideoGenerationHealth(
                available=False, login_state="UNKNOWN", detail=str(exc)[:500]
            )
        if login_required or not generation_ready:
            return VideoGenerationHealth(
                available=False,
                login_state="LOGIN_REQUIRED",
                detail=(
                    "Authenticate a Flow-capable subscription in the configured "
                    "persistent profile; the generation controls are unavailable"
                ),
            )
        return VideoGenerationHealth(available=True, login_state="AUTHENTICATED")

    def generate(
        self, scene: GenerationScene, destination: Path, preferred_model: str, mode: str
    ) -> Path:
        if self.chromium_executable is None:
            raise VideoGenerationError("Chromium executable is not configured")
        destination.parent.mkdir(parents=True, exist_ok=True)
        sync_playwright = self._playwright()
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(self.profile_path),
                executable_path=self.chromium_executable,
                headless=self.headless,
                accept_downloads=True,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(self.flow_url, wait_until="domcontentloaded", timeout=30_000)
                if "accounts.google" in page.url or page.get_by_text(
                    "Sign in", exact=False
                ).count() > 0:
                    raise FlowLoginRequired(
                        "Flow login required in the configured persistent browser profile"
                    )
                if page.locator(self.generate_selector).count() == 0:
                    raise FlowLoginRequired(
                        "Flow generation controls are unavailable; authenticate the "
                        "persistent profile and verify subscription/region access"
                    )
                constraints = ", ".join(scene.negative_constraints)
                prompt = scene.prompt
                if constraints:
                    prompt += f"\nNegative constraints: {constraints}"
                page.locator(self.prompt_selector).first.fill(prompt)
                page.locator(self.generate_selector).first.click()
                with page.expect_download(timeout=600_000) as download_info:
                    page.locator(self.download_selector).first.click(timeout=600_000)
                download_info.value.save_as(destination)
            finally:
                context.close()
        if not destination.is_file() or destination.stat().st_size == 0:
            raise VideoGenerationError("Flow download did not produce a non-empty file")
        return destination


class FlowBrowserVideoProvider(VideoGenerationProvider):
    def __init__(self, driver: FlowDriver, output_root: Path) -> None:
        self.driver = driver
        self.output_root = output_root.resolve()
        self.output_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def health(self) -> VideoGenerationHealth:
        return self.driver.health()

    def generate(self, request: VideoGenerationRequest) -> list[GeneratedVideo]:
        outputs: list[GeneratedVideo] = []
        credits_used = 0.0
        for scene in request.scenes:
            for expected_filename in scene.expected_filenames():
                last_error: Exception | None = None
                for attempt in range(1, scene.max_attempts + 1):
                    projected = credits_used + scene.estimated_credits_per_attempt
                    if projected > request.credit_budget:
                        raise GenerationBudgetError("generation credit budget exhausted")
                    credits_used = projected
                    try:
                        path = self.driver.generate(
                            scene,
                            self.output_root / expected_filename,
                            request.preferred_model,
                            request.mode,
                        )
                    except FlowLoginRequired:
                        raise
                    except Exception as exc:
                        last_error = exc
                        continue
                    outputs.append(
                        GeneratedVideo(
                            scene_id=scene.scene_id,
                            path=path,
                            provider="flow-browser",
                            model=request.preferred_model,
                            attempts=attempt,
                            credits=credits_used,
                        )
                    )
                    break
                else:
                    message = (
                        f"scene {scene.scene_id} failed after "
                        f"{scene.max_attempts} attempts: {last_error}"
                    )
                    raise VideoGenerationError(message)
        return outputs


class GenerationHandoffService:
    def __init__(self, services: Services) -> None:
        self.services = services

    def export(self, request: VideoGenerationRequest) -> HandoffPackage:
        workspace = self.services.storage.campaign_workspace(request.campaign_id)
        root = workspace / "handoffs" / "generation"
        references = root / "references"
        returned = root / "return"
        references.mkdir(parents=True, exist_ok=True)
        returned.mkdir(parents=True, exist_ok=True)
        serialized = request.model_dump(mode="json")
        for scene_payload, scene in zip(serialized["scenes"], request.scenes, strict=True):
            packaged: list[dict[str, str]] = []
            for reference in scene.reference_paths:
                source = self._campaign_reference(workspace, reference)
                target = references / f"{scene.scene_id}-{source.name}"
                shutil.copy2(source, target)
                packaged.append(
                    {
                        "path": str(target.relative_to(root)),
                        "checksum": sha256_file(target),
                    }
                )
            scene_payload["packaged_references"] = packaged
        (root / "GENERATION_REQUEST.json").write_text(
            json.dumps(serialized, indent=2, sort_keys=True) + "\n"
        )
        prompt_source = Path("prompts/GENERATION_HANDOFF_PROMPT_TEMPLATE.md")
        shutil.copy2(prompt_source, root / "EXECUTION_PROMPT.md")
        package = self.services.handoffs.save(
            HandoffPackage(
                campaign_id=request.campaign_id,
                handoff_type="generation",
                status="WAITING_FOR_RETURN",
                request_path=str(root.relative_to(workspace)),
                return_path=str(returned.relative_to(workspace)),
            )
        )
        self.services.ledger.append(
            LedgerEvent(
                campaign_id=request.campaign_id,
                stage="ASSET_GENERATION",
                event_type="generation_handoff_exported",
                status="WAITING_FOR_EXTERNAL_ASSET",
                details={"handoff_id": package.id, "scenes": len(request.scenes)},
            )
        )
        return package

    def import_return(
        self, package_id: str, request: VideoGenerationRequest
    ) -> list[Asset]:
        package = self.services.handoffs.get(package_id)
        if package is None or package.handoff_type != "generation":
            raise ReturnManifestError("generation handoff package not found")
        workspace = self.services.storage.campaign_workspace(package.campaign_id)
        return_root = workspace / package.return_path
        manifest_path = return_root / "GENERATION_RETURN_MANIFEST.json"
        if not manifest_path.is_file():
            raise ReturnManifestError("GENERATION_RETURN_MANIFEST.json is missing")
        manifest = json.loads(manifest_path.read_text())
        files = manifest.get("files")
        if not isinstance(files, list):
            raise ReturnManifestError("return manifest files must be an array")
        if request.campaign_id != package.campaign_id:
            raise ReturnManifestError("return request campaign does not match package")
        expected = {
            filename: scene
            for scene in request.scenes
            for filename in scene.expected_filenames()
        }
        provided = {item.get("filename"): item for item in files if isinstance(item, dict)}
        if set(provided) != set(expected):
            raise ReturnManifestError("return filenames do not exactly match the request")
        assets: list[Asset] = []
        manifest_value = self.services.storage.read_manifest(package.campaign_id)
        for filename, scene in expected.items():
            source = return_root / filename
            if not source.is_file() or source.stat().st_size == 0:
                raise ReturnManifestError(f"returned file missing or empty: {filename}")
            checksum = sha256_file(source)
            if checksum != provided[filename].get("checksum"):
                raise ReturnManifestError(f"checksum mismatch: {filename}")
            destination = workspace / "generated" / "video" / filename
            shutil.copy2(source, destination)
            asset = self.services.assets.save(
                Asset(
                    campaign_id=package.campaign_id,
                    asset_type="generated_video",
                    status="READY",
                    filepath=str(destination.relative_to(workspace)),
                    source="generation_handoff",
                    provider=provided[filename].get("provider"),
                    checksum=checksum,
                    provenance={
                        "scene_id": scene.scene_id,
                        "model": provided[filename].get("model"),
                        "attempts": provided[filename].get("attempts"),
                    },
                )
            )
            assets.append(asset)
            manifest_value["assets"].append(
                {
                    "asset_id": asset.id,
                    "asset_type": asset.asset_type,
                    "source": asset.source,
                    "provider": asset.provider,
                    "version": asset.version,
                    "status": asset.status,
                    "qc_score": asset.qc_score,
                    "filepath": asset.filepath,
                    "checksum": asset.checksum,
                    "used_in_final": asset.used_in_final,
                }
            )
        self.services.storage.write_manifest(package.campaign_id, manifest_value)
        self.services.handoffs.save(package.model_copy(update={"status": "IMPORTED"}))
        campaign = self.services.campaigns.get(package.campaign_id)
        if campaign and campaign.state == CampaignState.WAITING_FOR_EXTERNAL_ASSET:
            Orchestrator(self.services).resume(campaign.id)
        self.services.ledger.append(
            LedgerEvent(
                campaign_id=package.campaign_id,
                stage="ASSET_GENERATION",
                event_type="generation_handoff_imported",
                status="COMPLETE",
                output_asset_ids=[asset.id for asset in assets],
            )
        )
        return assets

    @staticmethod
    def _campaign_reference(workspace: Path, reference: str) -> Path:
        source = (workspace / reference).resolve()
        if not source.is_relative_to(workspace.resolve()):
            raise UnsafePathError("generation reference escapes campaign workspace")
        if not source.is_file():
            raise FileNotFoundError(reference)
        return source
