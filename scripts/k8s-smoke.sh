#!/usr/bin/env bash
# Throwaway Kubernetes check used by CI (and runnable locally):
#   k3d cluster create -> import images -> helm install (values-ci.yaml)
#   -> pods/services -> helm test -> HTTP smoke test through the ingress
#   -> helm uninstall -> k3d cluster delete (always, unless KEEP_CLUSTER=1).
# Needs k3d, kubectl, helm, and images meteorcloud/{backend,console}:$IMAGE_TAG.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART="$ROOT/deploy/kubernetes/helm/meteorcloud"
CLUSTER="${K8S_SMOKE_CLUSTER:-meteorcloud-ci}"
HTTP_PORT="${K8S_SMOKE_HTTP_PORT:-18088}"
IMAGE_TAG="${IMAGE_TAG:-ci}"
NAMESPACE=meteorcloud
RELEASE=meteorcloud
CTX="k3d-$CLUSTER"
KUBECTL=(kubectl --context "$CTX" -n "$NAMESPACE")

cleanup() {
  if [[ "${KEEP_CLUSTER:-}" == "1" ]]; then
    echo "==> Keeping cluster $CLUSTER (k3d cluster delete $CLUSTER)"
  else
    echo "==> Deleting cluster $CLUSTER"
    k3d cluster delete "$CLUSTER" || true
  fi
}
trap cleanup EXIT

diagnostics() {
  echo "==> Diagnostics"
  "${KUBECTL[@]}" get pods,services,ingress,pvc -o wide || true
  "${KUBECTL[@]}" describe pods || true
  for pod in $("${KUBECTL[@]}" get pods -o name 2>/dev/null); do
    echo "--- logs $pod"
    "${KUBECTL[@]}" logs "$pod" --all-containers --tail 100 || true
  done
}

echo "==> Creating k3d cluster $CLUSTER (ingress on localhost:$HTTP_PORT)"
k3d cluster create "$CLUSTER" --agents 0 --wait --timeout 300s \
  --kubeconfig-switch-context=false -p "$HTTP_PORT:80@loadbalancer"

echo "==> Importing images (tag $IMAGE_TAG)"
k3d image import -c "$CLUSTER" "meteorcloud/backend:$IMAGE_TAG" "meteorcloud/console:$IMAGE_TAG"

echo "==> helm install"
if ! helm install "$RELEASE" "$CHART" --kube-context "$CTX" -n "$NAMESPACE" --create-namespace \
  -f "$CHART/values-ci.yaml" \
  --set "backend.image.tag=$IMAGE_TAG" --set "console.image.tag=$IMAGE_TAG" \
  --set "config.publicUrl=http://localhost:$HTTP_PORT" \
  --wait --timeout 10m; then
  diagnostics
  exit 1
fi

"${KUBECTL[@]}" get pods,services,ingress,pvc

echo "==> helm test"
if ! helm test "$RELEASE" --kube-context "$CTX" -n "$NAMESPACE" --logs; then
  diagnostics
  exit 1
fi

echo "==> Waiting for the ingress controller"
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$HTTP_PORT/health" >/dev/null 2>&1; then
    break
  fi
  sleep 3
done

echo "==> Smoke test through the ingress"
if ! python3 "$ROOT/scripts/smoke_test.py" "http://127.0.0.1:$HTTP_PORT"; then
  diagnostics
  exit 1
fi

echo "==> helm uninstall"
helm uninstall "$RELEASE" --kube-context "$CTX" -n "$NAMESPACE" --wait --timeout 5m
remaining="$("${KUBECTL[@]}" get deployments,statefulsets,services,ingress -o name 2>/dev/null || true)"
if [[ -n "$remaining" ]]; then
  echo "error: resources left after uninstall:" >&2
  echo "$remaining" >&2
  exit 1
fi
echo "==> Kubernetes smoke test passed"
