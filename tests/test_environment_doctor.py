from __future__ import annotations

import pytest

from scripts import environment_doctor


def test_environment_report_is_safe_and_machine_readable() -> None:
    report = environment_doctor.build_report()
    assert report["required_ready"] is True
    rendered = str(report).lower()
    assert "email" not in rendered
    assert "token" not in rendered
    assert "cookie" not in rendered


def test_report_warns_when_unused_db_path_setting_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADFORGE_DB_PATH", "/opt/adforge/data/adforge.sqlite3")
    report = environment_doctor.build_report()
    assert any("ADFORGE_DB_PATH" in warning for warning in report["config_warnings"])


def test_report_has_no_warnings_when_db_path_setting_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ADFORGE_DB_PATH", raising=False)
    report = environment_doctor.build_report()
    assert report["config_warnings"] == []
