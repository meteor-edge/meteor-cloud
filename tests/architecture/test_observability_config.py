"""Consistency checks for the bundled observability config (stdlib only).

Dashboards and rules are plain files that nothing type-checks, so a renamed metric
would otherwise show up as an empty panel or an alert that can never fire.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
OBS = REPO / "deploy" / "compose" / "config" / "observability"
DASHBOARDS = sorted((OBS / "grafana" / "provisioning" / "dashboards" / "json").glob("*.json"))
DATASOURCES = OBS / "grafana" / "provisioning" / "datasources" / "datasources.yml"
RULE_FILES = sorted((OBS / "rules").glob("*.yml"))
METRIC_SOURCES = [
    REPO / "src" / "control-plane" / "app" / "core" / "metrics.py",
    REPO / "src" / "control-plane" / "app" / "devices" / "fleet_metrics.py",
    REPO / "src" / "data-plane" / "data_plane" / "metrics.py",
]

# Exporters we do not own: node_exporter, Prometheus' own `up`.
EXTERNAL_PREFIXES = ("node_",)
EXTERNAL_NAMES = {"up"}
# Names we check: everything our code or rules could have misspelled.
OWNED = re.compile(r"^(meteorcloud[_:]|http_)")
HISTOGRAM_SUFFIXES = ("_bucket", "_sum", "_count")


def _declared_metrics() -> set[str]:
    names: set[str] = set()
    for path in METRIC_SOURCES:
        names.update(re.findall(r"\"((?:meteorcloud|http)_[a-z0-9_]+)\"", path.read_text()))
    return names


def _recording_rules() -> set[str]:
    names: set[str] = set()
    for path in RULE_FILES:
        names.update(re.findall(r"^\s*-\s*record:\s*(\S+)", path.read_text(), flags=re.M))
    return names


def _metric_names(expr: str) -> set[str]:
    """Identifiers in a PromQL expression that look like metric names we own."""
    expr = re.sub(r"\"(?:[^\"\\]|\\.)*\"", '""', expr)  # drop label values
    expr = re.sub(r"\{[^}]*\}", "{}", expr)  # drop matchers (label names)
    expr = re.sub(r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)", "", expr)
    expr = re.sub(r"\[[^\]]*\]", "", expr)  # drop range selectors
    tokens = set(re.findall(r"[A-Za-z_:][A-Za-z0-9_:]*", expr))
    return {t for t in tokens if OWNED.match(t) or t in EXTERNAL_NAMES or t.startswith(EXTERNAL_PREFIXES)}


def _base_name(name: str) -> str:
    for suffix in HISTOGRAM_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _walk(node: object):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _prometheus_exprs(dashboard: dict) -> list[str]:
    exprs = []
    for node in _walk(dashboard):
        datasource = node.get("datasource")
        if isinstance(datasource, dict) and datasource.get("uid") == "prometheus":
            for target in node.get("targets", []):
                if target.get("datasource", datasource).get("uid") == "prometheus" and target.get("expr"):
                    exprs.append(target["expr"])
    return exprs


def _rule_exprs() -> list[str]:
    exprs = []
    for path in RULE_FILES:
        text = path.read_text()
        # `expr: |` blocks and single-line `expr:` values
        for match in re.finditer(r"^(\s*)expr:\s*\|\s*\n((?:\1\s+.*\n?)+)", text, flags=re.M):
            exprs.append(match.group(2))
        exprs.extend(m for m in re.findall(r"^\s*expr:\s*(?!\|)(.+)$", text, flags=re.M))
    return exprs


def _check_names(exprs: list[str]) -> list[str]:
    known = _declared_metrics() | _recording_rules()
    unknown = []
    for expr in exprs:
        for name in _metric_names(expr):
            if name in EXTERNAL_NAMES or name.startswith(EXTERNAL_PREFIXES):
                continue
            if name not in known and _base_name(name) not in known:
                unknown.append(f"{name} in {expr.strip()[:80]!r}")
    return unknown


def test_there_are_dashboards_and_rules() -> None:
    assert len(DASHBOARDS) >= 4
    assert RULE_FILES


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.name)
def test_dashboard_is_valid_provisioning_json(path: Path) -> None:
    dashboard = json.loads(path.read_text())
    assert dashboard.get("uid"), "dashboards need a stable uid so links survive re-provisioning"
    assert dashboard.get("title")
    assert dashboard.get("id") is None, "provisioned dashboards must not pin a database id"
    panel_ids = [p["id"] for p in dashboard.get("panels", [])]
    assert len(panel_ids) == len(set(panel_ids)), "panel ids must be unique"


def test_dashboard_uids_are_unique() -> None:
    uids = [json.loads(p.read_text())["uid"] for p in DASHBOARDS]
    assert len(uids) == len(set(uids))


def test_dashboards_use_only_provisioned_datasource_uids() -> None:
    provisioned = set(re.findall(r"^\s*uid:\s*(\S+)", DATASOURCES.read_text(), flags=re.M))
    assert provisioned == {"prometheus", "loki"}
    for path in DASHBOARDS:
        for node in _walk(json.loads(path.read_text())):
            datasource = node.get("datasource")
            if isinstance(datasource, dict) and "uid" in datasource:
                assert datasource["uid"] in provisioned, f"{path.name}: unknown datasource {datasource}"


def test_dashboard_queries_reference_declared_metrics() -> None:
    exprs = [e for path in DASHBOARDS for e in _prometheus_exprs(json.loads(path.read_text()))]
    assert exprs
    assert _check_names(exprs) == []


def test_rules_reference_declared_metrics() -> None:
    exprs = _rule_exprs()
    assert exprs
    assert _check_names(exprs) == []


def test_name_check_catches_a_typo() -> None:
    assert _check_names(['sum(rate(meteorcloud_devise_registrations_total[5m]))']) != []
    assert _check_names(['sum by (status) (rate(http_requests_total{status=~"5.."}[5m]))']) == []


HELM_RULES = REPO / "deploy" / "kubernetes" / "helm" / "meteorcloud" / "files" / "rules"


@pytest.mark.parametrize("name", ["alerts.yml", "recording.yml"])
def test_helm_rules_are_a_copy_of_the_compose_rules(name: str) -> None:
    # Helm can only read files inside the chart, so the rules are copied, not linked.
    assert (HELM_RULES / name).read_bytes() == (OBS / "rules" / name).read_bytes(), (
        f"cp {OBS.relative_to(REPO)}/rules/{name} {HELM_RULES.relative_to(REPO)}/"
    )
