#!/usr/bin/env bash
# Build & push the Northwind Bank image, then install/upgrade the Helm chart.
#
# Usage:
#   ./build-and-deploy.sh [--namespace NS] [--release NAME] [--no-build] [--llm] [--guardrails]
#
# Requires: docker (buildx), helm, kubectl, and a docker login to the target registry.
set -euo pipefail

REGISTRY="${REGISTRY:-ghcr.io}"
REPO="${REPO:-kastendevhub/northwind-bank}"
TAG="${TAG:-0.1.0}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
NAMESPACE="northwind-demo"
RELEASE="northwind"
DO_BUILD=1
EXTRA_SET=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace) NAMESPACE="$2"; shift 2;;
    --release) RELEASE="$2"; shift 2;;
    --no-build) DO_BUILD=0; shift;;
    --llm) EXTRA_SET+=(--set llm.enabled=true --set embedProvider=ollama); shift;;
    --guardrails) EXTRA_SET+=(--set app.guardrailsEnabled=true); shift;;
    *) echo "Unknown arg: $1"; exit 1;;
  esac
done

IMAGE="${REGISTRY}/${REPO}"

if [[ "$DO_BUILD" == "1" ]]; then
  echo "🏦 Building ${IMAGE}:${TAG} for ${PLATFORMS}..."
  docker buildx build --platform="${PLATFORMS}" --pull \
    -t "${IMAGE}:${TAG}" -t "${IMAGE}:latest" --push .
fi

echo "🚀 Deploying release '${RELEASE}' to namespace '${NAMESPACE}'..."
helm upgrade --install "${RELEASE}" ./charts/northwind-bank \
  --namespace "${NAMESPACE}" --create-namespace \
  --set image.registry="${REGISTRY}" \
  --set image.repository="${REPO}" \
  --set image.tag="${TAG}" \
  "${EXTRA_SET[@]}"

echo "⏳ Waiting for the web deployment to be ready..."
kubectl -n "${NAMESPACE}" rollout status "deploy/${RELEASE}-northwind-bank" --timeout=300s || true

echo "✅ Done."
kubectl -n "${NAMESPACE}" get pods,svc
echo
echo "Reach the portal:"
echo "  kubectl -n ${NAMESPACE} port-forward svc/${RELEASE}-northwind-bank 8080:80"
echo "Protect it with Kasten:"
echo "  kubectl apply -f kasten/blueprint.yaml && kubectl apply -f kasten/blueprintbinding.yaml"
