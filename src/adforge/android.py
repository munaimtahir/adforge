"""Safe APK ingestion, ADB capture operations, and emulator handoff protocol."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from adforge.models import Asset, CampaignState, HandoffPackage, LedgerEvent
from adforge.orchestrator import Orchestrator
from adforge.security import redact_text
from adforge.services import Services
from adforge.storage import UnsafePathError, safe_component, sha256_file


class AndroidError(RuntimeError):
    pass


class APKValidationError(AndroidError):
    pass


class CaptureReturnError(AndroidError):
    pass


class APKMetadata(BaseModel):
    original_path: Path
    copied_path: Path
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    package_id: str | None = None
    version_name: str | None = None
    version_code: str | None = None
    inspection_status: str


def parse_aapt_badging(output: str) -> tuple[str | None, str | None, str | None]:
    match = re.search(
        r"package: name='([^']+)' versionCode='([^']*)' versionName='([^']*)'", output
    )
    if match is None:
        return None, None, None
    return match.group(1), match.group(3) or None, match.group(2) or None


class APKIngestor:
    def __init__(self, services: Services, import_root: Path, aapt: str | None = None) -> None:
        self.services = services
        self.import_root = import_root.resolve()
        discovered = aapt or shutil.which("aapt") or self._sdk_aapt()
        self.aapt = str(Path(discovered).resolve()) if discovered else None

    def ingest(self, campaign_id: str, source_path: Path) -> APKMetadata:
        source = source_path.resolve()
        if not source.is_relative_to(self.import_root):
            raise APKValidationError("APK is outside the configured import root")
        if source.suffix.lower() != ".apk" or not source.is_file() or source.stat().st_size == 0:
            raise APKValidationError("APK must be an existing non-empty .apk file")
        original_checksum = sha256_file(source)
        original_stat = source.stat()
        workspace = self.services.storage.campaign_workspace(campaign_id)
        target = workspace / "app-capture" / "source.apk"
        shutil.copy2(source, target)
        copied_checksum = sha256_file(target)
        if copied_checksum != original_checksum:
            raise APKValidationError("copied APK checksum does not match original")
        source_changed = (
            sha256_file(source) != original_checksum
            or source.stat().st_mtime_ns != original_stat.st_mtime_ns
        )
        if source_changed:
            raise APKValidationError("source APK changed during ingestion")
        package_id = version_name = version_code = None
        inspection_status = "AAPT_UNAVAILABLE"
        if self.aapt:
            result = subprocess.run(  # noqa: S603 - resolved executable, fixed argv
                [self.aapt, "dump", "badging", str(target)],
                capture_output=True,
                check=False,
                text=True,
                timeout=30,
            )
            package_id, version_name, version_code = parse_aapt_badging(result.stdout)
            inspection_status = "INSPECTED" if package_id else "UNREADABLE"
        metadata = APKMetadata(
            original_path=source,
            copied_path=target,
            sha256=copied_checksum,
            package_id=package_id,
            version_name=version_name,
            version_code=version_code,
            inspection_status=inspection_status,
        )
        (target.parent / "apk-metadata.json").write_text(
            metadata.model_dump_json(indent=2) + "\n"
        )
        self.services.ledger.append(
            LedgerEvent(
                campaign_id=campaign_id,
                stage="APP_CAPTURE",
                event_type="apk_ingested",
                status="COMPLETE",
                details={
                    "sha256": copied_checksum,
                    "package_id": package_id,
                    "version_name": version_name,
                    "inspection_status": inspection_status,
                },
            )
        )
        return metadata

    @staticmethod
    def _sdk_aapt() -> str | None:
        sdk_root = Path.home() / "Android" / "Sdk" / "build-tools"
        candidates = sorted(sdk_root.glob("*/aapt"), reverse=True)
        return str(candidates[0]) if candidates else None


class ADBHealth(BaseModel):
    available: bool
    devices: list[str] = Field(default_factory=list)
    detail: str | None = None


class ADBAdapter:
    def __init__(self, executable: str | None = None, serial: str | None = None) -> None:
        resolved = executable or shutil.which("adb")
        self.executable = str(Path(resolved).resolve()) if resolved else None
        if serial and not re.fullmatch(r"[A-Za-z0-9._:-]+", serial):
            raise AndroidError("invalid ADB serial")
        self.serial = serial

    def health(self) -> ADBHealth:
        if not self.executable:
            return ADBHealth(available=False, detail="ADB executable not found")
        result = self._run(["devices", "-l"], timeout=15, check=False)
        devices = [
            line.split()[0]
            for line in result.stdout.splitlines()[1:]
            if " device " in f" {line} "
        ]
        return ADBHealth(
            available=result.returncode == 0 and bool(devices),
            devices=devices,
            detail=None if devices else "No ready ADB device",
        )

    def install(self, apk: Path) -> None:
        self._run(["install", "-r", str(apk.resolve())], timeout=180)

    def reset(self, package_id: str) -> None:
        self._package(package_id)
        self._run(["shell", "pm", "clear", package_id])

    def launch(self, package_id: str) -> None:
        self._package(package_id)
        self._run(
            ["shell", "monkey", "-p", package_id, "-c", "android.intent.category.LAUNCHER", "1"]
        )

    def tap(self, x: int, y: int) -> None:
        self._run(["shell", "input", "tap", str(x), str(y)])

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self._run(
            [
                "shell",
                "input",
                "swipe",
                str(x1),
                str(y1),
                str(x2),
                str(y2),
                str(duration_ms),
            ]
        )

    def type_text(self, value: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9 ._@+-]{1,200}", value):
            raise AndroidError("ADB text contains unsupported characters")
        self._run(["shell", "input", "text", value.replace(" ", "%s")])

    def screenshot(self, destination: Path) -> Path:
        result = self._run(["exec-out", "screencap", "-p"], text=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(result.stdout)
        return destination

    def screenrecord(self, destination: Path, duration_seconds: int) -> Path:
        if duration_seconds < 1 or duration_seconds > 180:
            raise AndroidError("screenrecord duration must be 1..180 seconds")
        filename = safe_component(destination.name)
        remote = f"/sdcard/{filename}"
        self._run(
            ["shell", "screenrecord", "--time-limit", str(duration_seconds), remote],
            timeout=duration_seconds + 30,
        )
        self._run(["pull", remote, str(destination.resolve())], timeout=60)
        self._run(["shell", "rm", remote])
        return destination

    def _run(
        self,
        arguments: list[str],
        *,
        timeout: float = 30,
        check: bool = True,
        text: bool = True,
    ) -> subprocess.CompletedProcess[Any]:
        if not self.executable:
            raise AndroidError("ADB executable not found")
        command = [self.executable]
        if self.serial:
            command.extend(["-s", self.serial])
        command.extend(arguments)
        try:
            result = subprocess.run(  # noqa: S603 - resolved adb with fixed argv
                command,
                capture_output=True,
                check=False,
                text=text,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise AndroidError(f"ADB operation timed out after {timeout:g}s") from exc
        if check and result.returncode != 0:
            stderr = redact_text(str(result.stderr))[-1000:]
            raise AndroidError(f"ADB operation failed: {stderr}")
        return result

    @staticmethod
    def _package(value: str) -> None:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+", value):
            raise AndroidError("invalid Android package ID")


class CaptureStep(BaseModel):
    action: Literal["tap", "swipe", "type", "wait", "screenshot", "screenrecord"]
    arguments: dict[str, Any] = Field(default_factory=dict)


class CaptureWorkflow(BaseModel):
    workflow_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    fictional_demo_data: dict[str, str]
    contains_private_data: bool = False
    steps: list[CaptureStep] = Field(min_length=1)

    @model_validator(mode="after")
    def private_data_is_forbidden(self) -> CaptureWorkflow:
        if self.contains_private_data:
            raise ValueError("real private demo data is forbidden")
        return self


class EmulatorCaptureRequest(BaseModel):
    campaign_id: str
    product_id: str
    apk_relative_path: str
    apk_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    package_id: str | None = None
    api_level: int = Field(default=35, ge=21)
    device_profile: str = "Pixel_6"
    orientation: Literal["portrait", "landscape"] = "portrait"
    resolution: str = Field(default="1080x1920", pattern=r"^[0-9]{3,4}x[0-9]{3,4}$")
    workflows: list[CaptureWorkflow] = Field(min_length=1)
    expected_filenames: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def filenames_are_safe(self) -> EmulatorCaptureRequest:
        for filename in self.expected_filenames:
            safe_component(filename)
            if Path(filename).suffix.lower() not in {".png", ".mp4"}:
                raise ValueError("capture output must be PNG or MP4")
        if len(set(self.expected_filenames)) != len(self.expected_filenames):
            raise ValueError("capture filenames must be unique")
        return self


class EmulatorHandoffService:
    def __init__(self, services: Services) -> None:
        self.services = services

    def export(self, request: EmulatorCaptureRequest) -> HandoffPackage:
        workspace = self.services.storage.campaign_workspace(request.campaign_id)
        apk = (workspace / request.apk_relative_path).resolve()
        if not apk.is_relative_to(workspace) or not apk.is_file():
            raise UnsafePathError("handoff APK escapes campaign workspace or is missing")
        if sha256_file(apk) != request.apk_sha256:
            raise APKValidationError("handoff APK checksum mismatch")
        root = workspace / "handoffs" / "emulator"
        returned = root / "return"
        returned.mkdir(parents=True, exist_ok=True)
        shutil.copy2(apk, root / "source.apk")
        (root / "CAPTURE_REQUEST.json").write_text(
            request.model_dump_json(indent=2) + "\n"
        )
        shutil.copy2(
            Path("prompts/EMULATOR_HANDOFF_PROMPT_TEMPLATE.md"),
            root / "EXECUTION_PROMPT.md",
        )
        package = self.services.handoffs.save(
            HandoffPackage(
                campaign_id=request.campaign_id,
                handoff_type="emulator_capture",
                status="WAITING_FOR_RETURN",
                request_path=str(root.relative_to(workspace)),
                return_path=str(returned.relative_to(workspace)),
                checksum=request.apk_sha256,
            )
        )
        self.services.ledger.append(
            LedgerEvent(
                campaign_id=request.campaign_id,
                stage="APP_CAPTURE",
                event_type="emulator_handoff_exported",
                status="WAITING_FOR_EXTERNAL_ASSET",
                details={"handoff_id": package.id, "apk_sha256": request.apk_sha256},
            )
        )
        return package

    def import_return(self, package_id: str, request: EmulatorCaptureRequest) -> list[Asset]:
        package = self.services.handoffs.get(package_id)
        if package is None or package.handoff_type != "emulator_capture":
            raise CaptureReturnError("emulator capture handoff not found")
        if package.campaign_id != request.campaign_id:
            raise CaptureReturnError("capture request campaign mismatch")
        workspace = self.services.storage.campaign_workspace(package.campaign_id)
        returned = workspace / package.return_path
        manifest_path = returned / "CAPTURE_RETURN_MANIFEST.json"
        if not manifest_path.is_file():
            raise CaptureReturnError("CAPTURE_RETURN_MANIFEST.json is missing")
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("apk_sha256") != request.apk_sha256:
            raise CaptureReturnError("capture used the wrong APK checksum")
        entries = manifest.get("files", [])
        provided = {item.get("filename"): item for item in entries if isinstance(item, dict)}
        if set(provided) != set(request.expected_filenames):
            raise CaptureReturnError("capture filenames do not exactly match request")
        assets: list[Asset] = []
        asset_manifest = self.services.storage.read_manifest(package.campaign_id)
        for filename in request.expected_filenames:
            source = returned / filename
            if not source.is_file() or source.stat().st_size == 0:
                raise CaptureReturnError(f"capture file missing or empty: {filename}")
            checksum = sha256_file(source)
            if checksum != provided[filename].get("checksum"):
                raise CaptureReturnError(f"capture checksum mismatch: {filename}")
            target = workspace / "app-capture" / filename
            shutil.copy2(source, target)
            asset = self.services.assets.save(
                Asset(
                    campaign_id=package.campaign_id,
                    asset_type=(
                        "app_capture_video"
                        if target.suffix == ".mp4"
                        else "app_capture_image"
                    ),
                    status="READY",
                    filepath=str(target.relative_to(workspace)),
                    source="emulator_handoff",
                    checksum=checksum,
                    provenance={
                        "apk_sha256": request.apk_sha256,
                        "device_profile": manifest.get("device_profile"),
                        "fictional_demo_data": True,
                    },
                )
            )
            assets.append(asset)
            asset_manifest["assets"].append(
                {
                    "asset_id": asset.id,
                    "asset_type": asset.asset_type,
                    "source": asset.source,
                    "provider": None,
                    "version": asset.version,
                    "status": asset.status,
                    "qc_score": None,
                    "filepath": asset.filepath,
                    "checksum": asset.checksum,
                    "used_in_final": False,
                }
            )
        self.services.storage.write_manifest(package.campaign_id, asset_manifest)
        self.services.handoffs.save(package.model_copy(update={"status": "IMPORTED"}))
        campaign = self.services.campaigns.get(package.campaign_id)
        if campaign and campaign.state == CampaignState.WAITING_FOR_EXTERNAL_ASSET:
            Orchestrator(self.services).resume(campaign.id)
        return assets
