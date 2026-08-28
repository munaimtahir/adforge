"""Replaceable structured AI provider contracts and subscription CLI adapters."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import jsonschema
from pydantic import BaseModel, ConfigDict, Field, model_validator

from adforge.models import LedgerEvent, ProviderExecution
from adforge.security import redact, redact_text
from adforge.services import Services
from adforge.storage import safe_component


class ProviderError(RuntimeError):
    pass


class ProviderUnavailableError(ProviderError):
    pass


class ProviderTimeoutError(ProviderError):
    pass


class StructuredOutputError(ProviderError):
    pass


class ProviderHealth(BaseModel):
    provider: str
    available: bool
    version: str | None = None
    detail: str | None = None
    capabilities: set[str] = Field(default_factory=set)


class ProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    task_type: str
    capability: str
    prompt: str = Field(min_length=1, max_length=100_000)
    context: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any]
    timeout_seconds: float = Field(default=180, gt=0, le=1800)

    @model_validator(mode="after")
    def reject_secrets(self) -> ProviderRequest:
        if redact_text(self.prompt) != self.prompt or redact(self.context) != self.context:
            raise ValueError("provider request contains secret-like material")
        jsonschema.Draft202012Validator.check_schema(self.output_schema)
        return self


class ProviderResponse(BaseModel):
    provider: str
    model: str | None = None
    output: dict[str, Any]
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = Field(ge=0)


class ReasoningProvider(ABC):
    name: str
    capabilities: set[str]

    @abstractmethod
    def health(self) -> ProviderHealth: ...

    @abstractmethod
    def execute(self, request: ProviderRequest) -> ProviderResponse: ...


class CLIProvider(ReasoningProvider):
    executable_name: str

    def __init__(self, workspace: Path, executable: str | None = None) -> None:
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        resolved = executable or shutil.which(self.executable_name)
        self.executable = str(Path(resolved).resolve()) if resolved else None

    def health(self) -> ProviderHealth:
        if self.executable is None or not Path(self.executable).is_file():
            return ProviderHealth(
                provider=self.name,
                available=False,
                detail=f"{self.executable_name} executable not found",
                capabilities=self.capabilities,
            )
        try:
            result = subprocess.run(  # noqa: S603 - executable is resolved, argv is fixed
                [self.executable, "--version"],
                capture_output=True,
                check=False,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ProviderHealth(
                provider=self.name,
                available=False,
                detail=redact_text(str(exc)),
                capabilities=self.capabilities,
            )
        version = (result.stdout or result.stderr).splitlines()
        return ProviderHealth(
            provider=self.name,
            available=result.returncode == 0,
            version=version[0] if version else None,
            detail=None if result.returncode == 0 else "version command failed",
            capabilities=self.capabilities,
        )

    def execute(self, request: ProviderRequest) -> ProviderResponse:
        if self.executable is None:
            raise ProviderUnavailableError(f"{self.name} is unavailable")
        request_dir = self.workspace / safe_component(request.request_id)
        request_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        command, output_path = self.build_command(request, request_dir)
        prompt = self.format_prompt(request)
        started = time.monotonic()
        try:
            result = subprocess.run(  # noqa: S603 - fixed argv, shell disabled
                command,
                input=prompt,
                cwd=request_dir,
                capture_output=True,
                check=False,
                text=True,
                timeout=request.timeout_seconds,
                env=self.safe_environment(),
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderTimeoutError(
                f"{self.name} timed out after {request.timeout_seconds:g}s"
            ) from exc
        except OSError as exc:
            raise ProviderUnavailableError(redact_text(str(exc))) from exc
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = redact_text(result.stdout)
        stderr = redact_text(result.stderr)
        if result.returncode != 0:
            raise ProviderError(
                f"{self.name} exited {result.returncode}: "
                f"{stderr[-1000:] or stdout[-1000:]}"
            )
        if output_path is not None and output_path.exists():
            raw = output_path.read_text()
        else:
            raw = stdout
        output = self.parse_output(raw)
        try:
            jsonschema.Draft202012Validator(request.output_schema).validate(output)
        except jsonschema.ValidationError as exc:
            raise StructuredOutputError(f"invalid structured output: {exc.message}") from exc
        return ProviderResponse(
            provider=self.name,
            output=output,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
        )

    def format_prompt(self, request: ProviderRequest) -> str:
        return (
            "Return only JSON conforming exactly to the supplied schema. "
            "Do not use tools or inspect unrelated files.\n\n"
            f"TASK:\n{request.prompt}\n\n"
            f"CONTEXT:\n{json.dumps(request.context, sort_keys=True)}\n\n"
            f"SCHEMA:\n{json.dumps(request.output_schema, sort_keys=True)}\n"
        )

    @staticmethod
    def safe_environment() -> dict[str, str]:
        allowed = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "CODEX_HOME", "CLAUDE_CONFIG_DIR")
        return {key: os.environ[key] for key in allowed if key in os.environ}

    @abstractmethod
    def build_command(
        self, request: ProviderRequest, request_dir: Path
    ) -> tuple[list[str], Path | None]: ...

    def parse_output(self, raw: str) -> dict[str, Any]:
        value = raw.strip()
        if value.startswith("```"):
            value = value.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise StructuredOutputError("provider did not return a JSON object") from exc
        if not isinstance(parsed, dict):
            raise StructuredOutputError("provider output must be a JSON object")
        return parsed


class ClaudeCodeProvider(CLIProvider):
    name = "claude-code"
    executable_name = "claude"
    capabilities = {"creative", "reasoning", "script", "storyboard", "product_truth_qc"}

    def build_command(
        self, request: ProviderRequest, request_dir: Path
    ) -> tuple[list[str], Path | None]:
        assert self.executable is not None
        return (
            [
                self.executable,
                "-p",
                "--output-format",
                "json",
                "--json-schema",
                json.dumps(request.output_schema, separators=(",", ":")),
                "--no-session-persistence",
                "--restricted",
                "--disable-slash-commands",
            ],
            None,
        )

    def parse_output(self, raw: str) -> dict[str, Any]:
        envelope = super().parse_output(raw)
        result = envelope.get("structured_output", envelope.get("result", envelope))
        if isinstance(result, str):
            return super().parse_output(result)
        if not isinstance(result, dict):
            raise StructuredOutputError("Claude result is not an object")
        return result


class CodexCLIProvider(CLIProvider):
    name = "codex-cli"
    executable_name = "codex"
    capabilities = {"creative", "reasoning", "technical", "script", "storyboard"}

    def build_command(
        self, request: ProviderRequest, request_dir: Path
    ) -> tuple[list[str], Path | None]:
        assert self.executable is not None
        schema_path = request_dir / "output-schema.json"
        output_path = request_dir / "last-message.json"
        schema_path.write_text(json.dumps(request.output_schema))
        return (
            [
                self.executable,
                "exec",
                "--sandbox",
                "read-only",
                "--ephemeral",
                "--skip-git-repo-check",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "-",
            ],
            output_path,
        )


class ProviderRouter:
    def __init__(
        self,
        providers: list[ReasoningProvider],
        preferences: dict[str, list[str]] | None = None,
    ) -> None:
        self.providers = providers
        self.preferences = preferences or {}

    def select(self, request: ProviderRequest) -> ReasoningProvider:
        available = [
            provider
            for provider in self.providers
            if request.capability in provider.capabilities and provider.health().available
        ]
        if not available:
            raise ProviderUnavailableError(
                f"no healthy provider supports capability: {request.capability}"
            )
        preferred_names = self.preferences.get(request.task_type, [])
        rank = {name: index for index, name in enumerate(preferred_names)}
        return min(available, key=lambda provider: rank.get(provider.name, len(rank)))


class ProviderExecutor:
    def __init__(self, services: Services) -> None:
        self.services = services

    def execute(
        self,
        campaign_id: str,
        task_id: str,
        provider: ReasoningProvider,
        request: ProviderRequest,
    ) -> ProviderResponse:
        last_error: ProviderError | None = None
        for attempt in range(1, 4):
            try:
                response = provider.execute(request)
            except ProviderError as exc:
                last_error = exc
                self._record(
                    campaign_id,
                    task_id,
                    provider.name,
                    attempt,
                    "FAILED",
                    stderr=str(exc),
                )
                continue
            self._record(
                campaign_id,
                task_id,
                provider.name,
                attempt,
                "COMPLETE",
                response=response,
            )
            return response
        raise ProviderUnavailableError(
            f"{provider.name} failed after initial attempt and two retries: {last_error}"
        ) from last_error

    def _record(
        self,
        campaign_id: str,
        task_id: str,
        provider: str,
        attempt: int,
        status: str,
        *,
        response: ProviderResponse | None = None,
        stderr: str = "",
    ) -> None:
        execution = self.services.provider_executions.save(
            ProviderExecution(
                campaign_id=campaign_id,
                task_id=task_id,
                provider=provider,
                attempt=attempt,
                status=status,
                stdout=redact_text(response.stdout if response else ""),
                stderr=redact_text(stderr or (response.stderr if response else "")),
                duration_ms=response.duration_ms if response else None,
            )
        )
        self.services.ledger.append(
            LedgerEvent(
                campaign_id=campaign_id,
                stage="PROVIDER_EXECUTION",
                task_id=task_id,
                event_type="provider_execution",
                provider=provider,
                attempt=attempt,
                status=status,
                details={"provider_execution_id": execution.id},
            )
        )
