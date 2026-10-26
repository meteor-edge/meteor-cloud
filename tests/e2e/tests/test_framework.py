from e2e.report import BLOCKED, FAIL, NOT_SUPPORTED, PASS, Report
from e2e.suite.framework import Blocked, Check, Fail, NotSupported, Runner, expect


def run(checks):
    report = Report(env_name="ci", run_id="r")
    Runner(ctx=None, report=report).run(checks)
    return {r.id: r for r in report.results}


def test_dependencies_block_dependents() -> None:
    def bad(ctx):
        raise Fail("nope")

    results = run([
        Check("a", "s", "a", lambda ctx: {"ok": 1}),
        Check("b", "s", "b", bad, needs=("a",)),
        Check("c", "s", "c", lambda ctx: None, needs=("b",)),
        Check("d", "s", "d", lambda ctx: None, needs=("a",)),
    ])
    assert results["a"].status == PASS and results["a"].evidence == {"ok": 1}
    assert results["b"].status == FAIL and results["b"].detail == "nope"
    assert results["c"].status == BLOCKED and "b=fail" in results["c"].detail
    assert results["d"].status == PASS


def test_blocked_not_supported_and_errors() -> None:
    def blocked(ctx):
        raise Blocked("no domain")

    def unsupported(ctx):
        raise NotSupported("no api")

    def boom(ctx):
        raise RuntimeError("token=abcdefghijkl")

    results = run([Check("x", "s", "x", blocked), Check("y", "s", "y", unsupported), Check("z", "s", "z", boom)])
    assert results["x"].status == BLOCKED
    assert results["y"].status == NOT_SUPPORTED
    assert results["z"].status == FAIL and "abcdefghijkl" not in results["z"].detail


def test_not_supported_dependency_cascades() -> None:
    def unsupported(ctx):
        raise NotSupported("no domain")

    results = run([
        Check("dns", "s", "dns", unsupported),
        Check("tls", "s", "tls", lambda ctx: None, needs=("dns",)),
        Check("app", "s", "app", lambda ctx: None, needs=("tls",)),
    ])
    assert results["dns"].status == NOT_SUPPORTED
    assert results["tls"].status == NOT_SUPPORTED and "dns=not_supported" in results["tls"].detail
    assert results["app"].status == NOT_SUPPORTED and "tls=not_supported" in results["app"].detail


def test_fail_still_blocks_even_if_another_need_is_unsupported() -> None:
    def unsupported(ctx):
        raise NotSupported("no domain")

    def broken(ctx):
        raise Fail("boom")

    results = run([
        Check("ok", "s", "ok", lambda ctx: None),
        Check("dns", "s", "dns", unsupported),
        Check("broken", "s", "broken", broken),
        Check("mix", "s", "mix", lambda ctx: None, needs=("ok", "dns", "broken")),
    ])
    assert results["mix"].status == BLOCKED
    assert "broken=fail" in results["mix"].detail


def test_when_filters_and_expect() -> None:
    results = run([Check("w", "s", "w", lambda ctx: expect(False, "x"), when=lambda ctx: False)])
    assert results == {}
