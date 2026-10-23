import json

import pytest

from e2e.report import BLOCKED, FAIL, NOT_SUPPORTED, PASS, Report, Result


def make(*statuses: str) -> Report:
    report = Report(env_name="ci", run_id="r1")
    for i, status in enumerate(statuses):
        report.add(Result(f"c{i}", "stage", f"check {i}", status))
    return report


def test_ok_only_without_fail_or_blocked() -> None:
    assert make(PASS, PASS).ok
    assert make(PASS, NOT_SUPPORTED).ok
    assert not make(PASS, BLOCKED).ok
    assert not make(PASS, FAIL).ok
    assert not make().ok


def test_unknown_status_rejected() -> None:
    with pytest.raises(ValueError):
        Result("x", "s", "t", "skipped")


def test_write_redacts(tmp_path) -> None:
    report = make(FAIL)
    report.results[0].detail = "password=hunter2hunter2"
    json_path, text_path = report.write(tmp_path)
    assert "hunter2" not in json_path.read_text()
    assert "hunter2" not in text_path.read_text()
    assert json.loads(json_path.read_text())["counts"][FAIL] == 1
