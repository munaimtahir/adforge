from __future__ import annotations

from scripts import environment_doctor


def test_environment_report_is_safe_and_machine_readable() -> None:
    report = environment_doctor.build_report()
    assert report["required_ready"] is True
    rendered = str(report).lower()
    assert "email" not in rendered
    assert "token" not in rendered
    assert "cookie" not in rendered
