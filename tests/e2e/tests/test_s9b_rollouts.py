from types import SimpleNamespace

from e2e.report import NOT_SUPPORTED, PASS, Report
from e2e.suite import s9b_rollouts, s9c_rollout_kinds
from e2e.suite.framework import REGISTRY, Check, Runner


def test_rollout_checks_are_skipped_with_fewer_than_five_devices() -> None:
    ctx = SimpleNamespace(devices=[object(), object()])
    rollout = [c for c in REGISTRY if c.stage == s9b_rollouts.STAGE]
    assert [c.id for c in rollout] == [
        "rollout.fleet_onboard",
        "rollout.phased_soak",
        "rollout.pause_resume",
        "rollout.failure_threshold_rollback",
        "rollout.batched",
        "rollout.pending_timeout",
    ]
    report = Report(env_name="ci", run_id="r")
    passed = [Check(n, "s", n, lambda ctx: None) for n in ("device.online", "app.image_artifact")]
    Runner(ctx=ctx, report=report).run(passed + rollout)
    statuses = {r.id: r.status for r in report.results}
    assert statuses.pop("device.online") == PASS and statuses.pop("app.image_artifact") == PASS
    assert set(statuses.values()) == {NOT_SUPPORTED}


def test_parse_time_accepts_z_suffix() -> None:
    assert s9b_rollouts.parse_time("2026-11-02T12:00:00Z").utcoffset().total_seconds() == 0


def test_compose_file_renders_port_and_env_references() -> None:
    rendered = s9c_rollout_kinds.COMPOSE_FILE.format(port=8082)
    assert '"8082:8080"' in rendered
    assert "RUN_ID: ${RUN_ID}" in rendered and "$$VERSION" in rendered
