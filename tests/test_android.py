from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from adforge.android import (
    APKIngestor,
    APKValidationError,
    CaptureReturnError,
    CaptureStep,
    CaptureWorkflow,
    EmulatorCaptureRequest,
    EmulatorHandoffService,
    parse_aapt_badging,
)
from adforge.models import Campaign, CampaignState
from adforge.services import Services
from adforge.storage import sha256_file


def test_apk_copy_preserves_original_and_records_checksum(tmp_path: Path) -> None:
    services = Services(tmp_path / "runtime", Path("schemas"))
    services.initialize()
    campaign = services.campaigns.save(
        Campaign(product_id="product", name="Capture", brief="Capture proof")
    )
    imports = tmp_path / "imports"
    imports.mkdir()
    source = imports / "fixture.apk"
    source.write_bytes(b"not a real apk but immutable ingestion fixture")
    before = source.read_bytes()
    metadata = APKIngestor(services, imports, aapt="/bin/false").ingest(
        campaign.id, source
    )
    assert source.read_bytes() == before
    assert metadata.sha256 == sha256_file(source) == sha256_file(metadata.copied_path)
    assert metadata.inspection_status == "UNREADABLE"
    with pytest.raises(APKValidationError, match="outside"):
        APKIngestor(services, imports).ingest(campaign.id, Path("/etc/passwd"))


def test_aapt_package_version_parser() -> None:
    output = "package: name='app.fixture' versionCode='42' versionName='2.1.0'"
    assert parse_aapt_badging(output) == ("app.fixture", "2.1.0", "42")


def capture_request(campaign_id: str, checksum: str) -> EmulatorCaptureRequest:
    return EmulatorCaptureRequest(
        campaign_id=campaign_id,
        product_id="product",
        apk_relative_path="app-capture/source.apk",
        apk_sha256=checksum,
        package_id="app.fixture",
        workflows=[
            CaptureWorkflow(
                workflow_id="proof",
                fictional_demo_data={"owner": "Fictional Homeowner"},
                steps=[CaptureStep(action="tap", arguments={"x": 10, "y": 20})],
            )
        ],
        expected_filenames=["proof.png", "proof.mp4"],
    )


def test_private_demo_data_and_unsafe_filenames_are_rejected() -> None:
    with pytest.raises(ValidationError, match="private demo data"):
        CaptureWorkflow(
            workflow_id="bad",
            fictional_demo_data={"name": "real user"},
            contains_private_data=True,
            steps=[CaptureStep(action="wait")],
        )
    with pytest.raises(ValidationError):
        EmulatorCaptureRequest(
            campaign_id="campaign",
            product_id="product",
            apk_relative_path="source.apk",
            apk_sha256="a" * 64,
            workflows=[
                CaptureWorkflow(
                    workflow_id="safe",
                    fictional_demo_data={"name": "fictional"},
                    steps=[CaptureStep(action="wait")],
                )
            ],
            expected_filenames=["../escape.mp4"],
        )


def test_emulator_handoff_fixture_round_trip_and_resume(tmp_path: Path) -> None:
    services = Services(tmp_path / "runtime", Path("schemas"))
    services.initialize()
    campaign = services.campaigns.save(
        Campaign(
            product_id="product",
            name="Capture",
            brief="Capture proof",
            state=CampaignState.WAITING_FOR_EXTERNAL_ASSET,
            resume_state=CampaignState.APP_CAPTURE,
        )
    )
    workspace = services.storage.campaign_workspace(campaign.id)
    apk = workspace / "app-capture" / "source.apk"
    apk.write_bytes(b"fixture apk")
    request = capture_request(campaign.id, sha256_file(apk))
    service = EmulatorHandoffService(services)
    package = service.export(request)
    returned = workspace / package.return_path
    files = []
    for filename in request.expected_filenames:
        path = returned / filename
        path.write_bytes(f"fixture {filename}".encode())
        files.append({"filename": filename, "checksum": sha256_file(path)})
    (returned / "CAPTURE_RETURN_MANIFEST.json").write_text(
        json.dumps(
            {
                "apk_sha256": request.apk_sha256,
                "device_profile": "fixture-emulator",
                "files": files,
            }
        )
    )
    assets = service.import_return(package.id, request)
    assert len(assets) == 2
    assert all(asset.provenance["fictional_demo_data"] for asset in assets)
    resumed = services.campaigns.get(campaign.id)
    assert resumed is not None and resumed.state == CampaignState.APP_CAPTURE


def test_emulator_handoff_rejects_wrong_return(tmp_path: Path) -> None:
    services = Services(tmp_path / "runtime", Path("schemas"))
    services.initialize()
    campaign = services.campaigns.save(
        Campaign(product_id="product", name="Capture", brief="Capture proof")
    )
    workspace = services.storage.campaign_workspace(campaign.id)
    apk = workspace / "app-capture" / "source.apk"
    apk.write_bytes(b"fixture apk")
    request = capture_request(campaign.id, sha256_file(apk))
    service = EmulatorHandoffService(services)
    package = service.export(request)
    returned = workspace / package.return_path
    (returned / "CAPTURE_RETURN_MANIFEST.json").write_text(
        json.dumps({"apk_sha256": "b" * 64, "files": []})
    )
    with pytest.raises(CaptureReturnError, match="wrong APK"):
        service.import_return(package.id, request)
