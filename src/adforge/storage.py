"""Local storage with containment checks and campaign workspace isolation."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import jsonschema

SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
WORKSPACE_DIRS = (
    "brief",
    "truth",
    "strategy",
    "script",
    "storyboard",
    "asset-plan",
    "generated/images",
    "generated/video",
    "app-capture",
    "audio/voice",
    "audio/music",
    "audio/sfx",
    "edit",
    "renders/drafts",
    "renders/final",
    "qc",
    "handoffs",
)


class UnsafePathError(ValueError):
    pass


def safe_component(value: str) -> str:
    if not SAFE_COMPONENT.fullmatch(value) or value in {".", ".."}:
        raise UnsafePathError("path component is not allowed")
    return value


def contained_path(root: Path, *components: str) -> Path:
    clean = [safe_component(component) for component in components]
    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*clean).resolve()
    if not candidate.is_relative_to(resolved_root):
        raise UnsafePathError("path escapes storage root")
    return candidate


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LocalStorage:
    def __init__(self, root: Path, schema_root: Path) -> None:
        self.root = root.resolve()
        self.schema_root = schema_root.resolve()

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        directories = (
            "data",
            "products",
            "campaigns",
            "assets",
            "exports",
            "logs",
            "backups",
            "temp",
            "browser-profiles",
        )
        for directory in directories:
            path = contained_path(self.root, directory)
            path.mkdir(exist_ok=True, mode=0o700)
        os.chmod(contained_path(self.root, "browser-profiles"), 0o700)

    def campaign_workspace(self, campaign_id: str, *, create: bool = True) -> Path:
        workspace = contained_path(self.root, "campaigns", campaign_id)
        if create:
            workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
            for relative in WORKSPACE_DIRS:
                workspace.joinpath(*relative.split("/")).mkdir(parents=True, exist_ok=True)
            manifest = workspace / "manifest.json"
            if not manifest.exists():
                self.write_manifest(campaign_id, {"campaign_id": campaign_id, "assets": []})
            (workspace / "production-ledger.jsonl").touch(exist_ok=True)
        return workspace

    def campaign_path(self, campaign_id: str, *components: str) -> Path:
        workspace = self.campaign_workspace(campaign_id, create=False)
        candidate = contained_path(workspace, *components)
        if not candidate.is_relative_to(workspace):
            raise UnsafePathError("path escapes campaign workspace")
        return candidate

    def validate_manifest(self, manifest: dict[str, Any]) -> None:
        schema = json.loads((self.schema_root / "asset_manifest.schema.json").read_text())
        jsonschema.Draft202012Validator(schema).validate(manifest)

    def write_manifest(self, campaign_id: str, manifest: dict[str, Any]) -> Path:
        if manifest.get("campaign_id") != campaign_id:
            raise ValueError("manifest campaign does not match workspace")
        self.validate_manifest(manifest)
        path = self.campaign_path(campaign_id, "manifest.json")
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        temporary.replace(path)
        return path

    def read_manifest(self, campaign_id: str) -> dict[str, Any]:
        value: dict[str, Any] = json.loads(
            self.campaign_path(campaign_id, "manifest.json").read_text()
        )
        self.validate_manifest(value)
        return value
