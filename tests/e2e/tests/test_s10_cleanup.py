from types import SimpleNamespace

from e2e.suite.s10_cleanup import collect_http_routes, workload_removal_ops


def test_collect_http_routes_skips_includes_without_path() -> None:
    inner = SimpleNamespace(path="/api/v1/deployments/{deployment_id}", methods={"GET", "DELETE"})
    included = SimpleNamespace(routes=[inner])  # FastAPI _IncludedRouter: no path
    mount = SimpleNamespace()  # Mount: no path, no nested routes
    listed = collect_http_routes([included, mount, inner])
    assert listed == [
        ["/api/v1/deployments/{deployment_id}", ["DELETE", "GET"]],
        ["/api/v1/deployments/{deployment_id}", ["DELETE", "GET"]],
    ]


def test_workload_removal_ignores_deploy_cancel() -> None:
    routes = [
        ["/api/v1/organizations/{org}/deployments/{id}", ["DELETE", "GET"]],
        ["/api/v1/organizations/{org}/devices/{id}/software", ["GET"]],
    ]
    assert workload_removal_ops(routes) == []


def test_workload_removal_finds_software_delete() -> None:
    routes = [
        ["/api/v1/organizations/{org}/devices/{id}/software/{key}", ["DELETE"]],
    ]
    assert workload_removal_ops(routes) == [
        "DELETE /api/v1/organizations/{org}/devices/{id}/software/{key}",
    ]
