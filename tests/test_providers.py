from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from adforge.providers import (
    ClaudeCodeProvider,
    ProviderError,
    ProviderExecutor,
    ProviderHealth,
    ProviderRequest,
    ProviderResponse,
    ProviderRouter,
    ProviderUnavailableError,
    ReasoningProvider,
    StructuredOutputError,
)
from adforge.services import Services

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def request(**updates: Any) -> ProviderRequest:
    values: dict[str, Any] = {
        "request_id": "request-1",
        "task_type": "script",
        "capability": "creative",
        "prompt": "Write one truthful line.",
        "context": {"approved": ["Feature"]},
        "output_schema": SCHEMA,
        "timeout_seconds": 1,
    }
    values.update(updates)
    return ProviderRequest(**values)


class StubProvider(ReasoningProvider):
    def __init__(self, name: str, *, healthy: bool = True, failures: int = 0) -> None:
        self.name = name
        self.capabilities = {"creative"}
        self.healthy = healthy
        self.failures = failures
        self.calls = 0

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            available=self.healthy,
            capabilities=self.capabilities,
        )

    def execute(self, _: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        if self.calls <= self.failures:
            raise ProviderError("Bearer secret-provider-token")
        return ProviderResponse(
            provider=self.name,
            output={"answer": "Feature"},
            stdout="ok",
            duration_ms=1,
        )


def test_provider_request_rejects_secret_material() -> None:
    with pytest.raises(ValidationError, match="secret-like"):
        request(context={"api_token": "token-secret-value"})
    with pytest.raises(ValidationError, match="secret-like"):
        request(prompt="Use Bearer abcdefghijk")


def test_router_uses_capability_health_and_configurable_preference() -> None:
    first = StubProvider("claude-code")
    second = StubProvider("codex-cli")
    router = ProviderRouter([second, first], {"script": ["claude-code", "codex-cli"]})
    assert router.select(request()) is first
    first.healthy = False
    assert router.select(request()) is second
    second.healthy = False
    with pytest.raises(ProviderUnavailableError, match="no healthy provider"):
        router.select(request())


def test_cli_uses_fixed_argv_and_validates_structured_output(tmp_path: Path) -> None:
    provider = ClaudeCodeProvider(tmp_path, executable="/bin/echo")
    command, _ = provider.build_command(request(prompt="$(touch /tmp/pwned); `id`"), tmp_path)
    assert command[0] == "/usr/bin/echo"
    assert "touch" not in command
    assert provider.parse_output('{"result":"{\\"answer\\":\\"ok\\"}"}') == {
        "answer": "ok"
    }
    with pytest.raises(StructuredOutputError):
        provider.parse_output("not-json")


def test_executor_retries_twice_then_records_blocked_diagnostics(tmp_path: Path) -> None:
    services = Services(tmp_path / "runtime", Path("schemas"))
    services.initialize()
    services.storage.campaign_workspace("campaign-1")
    provider = StubProvider("fixture", failures=3)
    with pytest.raises(ProviderUnavailableError, match="two retries"):
        ProviderExecutor(services).execute("campaign-1", "task-1", provider, request())
    assert provider.calls == 3
    executions = services.provider_executions.list()
    assert len(executions) == 3
    assert all("secret-provider-token" not in item.stderr for item in executions)


def test_executor_succeeds_on_retry_and_ledgers_execution(tmp_path: Path) -> None:
    services = Services(tmp_path / "runtime", Path("schemas"))
    services.initialize()
    services.storage.campaign_workspace("campaign-1")
    provider = StubProvider("fixture", failures=2)
    response = ProviderExecutor(services).execute(
        "campaign-1", "task-1", provider, request()
    )
    assert response.output == {"answer": "Feature"}
    assert provider.calls == 3
    assert len(services.ledger.read("campaign-1")) == 3
