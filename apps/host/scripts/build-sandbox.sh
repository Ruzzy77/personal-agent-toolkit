#!/usr/bin/env bash
# Build named Personal Agent Host sandbox profiles from this repository.
# Document Files is resolved from a minimal repository-shaped build context.
set -euo pipefail

REPO="${1:-$(cd "$(dirname "$0")/../../.." && pwd)}"
TARGET="${2:-all}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required to build sandbox images" >&2
  exit 1
fi

build_base() {
  docker build -t personal-agent-host-sandbox:1 "$REPO/plugins/host/sandbox"
}

build_web() {
  docker image inspect personal-agent-host-sandbox:1 >/dev/null
  docker build -f "$REPO/plugins/host/sandbox/Dockerfile.web" \
    -t personal-agent-host-web:1 "$REPO/plugins/host/sandbox"
}

build_documents() (
  context="$(mktemp -d)"
  trap 'rm -rf "$context"' EXIT
  mkdir -p "$context/scripts" "$context/dependencies" \
    "$context/plugins/document-files"
  cp "$REPO/scripts/document_files_release.py" "$context/scripts/"
  cp "$REPO/dependencies/document-files.json" "$context/dependencies/"
  cp "$REPO/plugins/document-files/pyproject.toml" \
    "$REPO/plugins/document-files/uv.lock" "$context/plugins/document-files/"
  cp -a "$REPO/plugins/document-files/src" "$context/plugins/document-files/"
  docker image inspect personal-agent-host-sandbox:1 >/dev/null
  docker build -f "$REPO/plugins/host/sandbox/Dockerfile.documents" \
    -t personal-agent-host-documents:1 "$context"
)

case "$TARGET" in
  base) build_base ;;
  web) build_web ;;
  documents) build_documents ;;
  all)
    build_base
    build_web
    build_documents
    ;;
  *)
    echo "usage: $0 [repo] [base|web|documents|all]" >&2
    exit 2
    ;;
esac
