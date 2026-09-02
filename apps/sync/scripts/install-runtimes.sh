#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../../.." && pwd)
RUNTIME_ROOT=${1:-"$HOME/Library/Application Support/Personal Agent Sync/runtimes"}

command -v uv >/dev/null 2>&1 || {
  printf '%s\n' 'uv is required to install Personal Agent Sync runtimes.' >&2
  exit 1
}

umask 077
mkdir -p "$RUNTIME_ROOT"

install_runtime() {
  name=$1
  package=$2
  destination="$RUNTIME_ROOT/$name"
  uv venv --clear "$destination"
  uv pip install --python "$destination/bin/python" "$package"
}

install_runtime sync "$REPOSITORY_ROOT/apps/sync"
install_runtime corpus "$REPOSITORY_ROOT/plugins/corpus"
install_runtime document-files "$REPOSITORY_ROOT/plugins/document-files"

printf '%s\n' "$RUNTIME_ROOT/sync/bin/personal-agent-sync"
printf '%s\n' "$RUNTIME_ROOT/corpus/bin/python"
printf '%s\n' "$RUNTIME_ROOT/document-files/bin/python"
