#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../../.." && pwd)
DEFAULT_RUNTIME_ROOT="$HOME/Library/Application Support/Personal Agent Sync/runtimes"
RUNTIME_ROOT=${1:-"$DEFAULT_RUNTIME_ROOT"}
STAGING_ROOT="${RUNTIME_ROOT}.install.$$"
BACKUP_ROOT="${RUNTIME_ROOT}.backup.$$"
AGENT_DOMAIN="gui/$(id -u)"
AGENT_LABEL="dev.personal-agent.sync"
AGENT_PLIST="$HOME/Library/LaunchAgents/$AGENT_LABEL.plist"
AGENT_WAS_LOADED=0

case "$RUNTIME_ROOT" in
  /*) ;;
  *)
    printf '%s\n' 'The Personal Agent Sync runtime root must be an absolute path.' >&2
    exit 1
    ;;
esac
[ "$RUNTIME_ROOT" != "/" ] || {
  printf '%s\n' 'The filesystem root cannot be used as the Sync runtime root.' >&2
  exit 1
}

command -v uv >/dev/null 2>&1 || {
  printf '%s\n' 'uv is required to install Personal Agent Sync runtimes.' >&2
  exit 1
}

umask 077
mkdir -p "$(dirname -- "$RUNTIME_ROOT")"

cleanup_staging() {
  rm -rf "$STAGING_ROOT"
}
trap cleanup_staging EXIT HUP INT TERM

rm -rf "$STAGING_ROOT" "$BACKUP_ROOT"
mkdir -p "$STAGING_ROOT"

install_runtime() {
  name=$1
  package=$2
  destination="$STAGING_ROOT/$name"
  uv venv --relocatable "$destination"
  uv pip install --python "$destination/bin/python" "$package"
}

sync_destination="$STAGING_ROOT/sync"
uv venv --relocatable "$sync_destination"
uv pip install --python "$sync_destination/bin/python" \
  "$REPOSITORY_ROOT/plugins/document-files" "$REPOSITORY_ROOT/apps/sync"
install_runtime corpus "$REPOSITORY_ROOT/engines/corpus"
"$sync_destination/bin/python" \
  "$REPOSITORY_ROOT/plugins/document-files/scripts/provision_rhwp.py" >/dev/null

if [ "$RUNTIME_ROOT" = "$DEFAULT_RUNTIME_ROOT" ] &&
  launchctl print "$AGENT_DOMAIN/$AGENT_LABEL" >/dev/null 2>&1; then
  AGENT_WAS_LOADED=1
  launchctl bootout "$AGENT_DOMAIN/$AGENT_LABEL"
fi

if [ -e "$RUNTIME_ROOT" ]; then
  if ! mv "$RUNTIME_ROOT" "$BACKUP_ROOT"; then
    if [ "$AGENT_WAS_LOADED" -eq 1 ]; then
      launchctl bootstrap "$AGENT_DOMAIN" "$AGENT_PLIST" || true
    fi
    printf '%s\n' 'Existing Personal Agent Sync runtimes could not be staged for replacement.' >&2
    exit 1
  fi
fi

if ! mv "$STAGING_ROOT" "$RUNTIME_ROOT"; then
  if [ -e "$BACKUP_ROOT" ]; then
    mv "$BACKUP_ROOT" "$RUNTIME_ROOT"
  fi
  if [ "$AGENT_WAS_LOADED" -eq 1 ]; then
    launchctl bootstrap "$AGENT_DOMAIN" "$AGENT_PLIST" || true
  fi
  printf '%s\n' 'New Personal Agent Sync runtimes could not be activated.' >&2
  exit 1
fi

if [ "$AGENT_WAS_LOADED" -eq 1 ]; then
  if ! launchctl bootstrap "$AGENT_DOMAIN" "$AGENT_PLIST"; then
    FAILED_ROOT="${RUNTIME_ROOT}.failed.$$"
    mv "$RUNTIME_ROOT" "$FAILED_ROOT"
    if [ -e "$BACKUP_ROOT" ]; then
      mv "$BACKUP_ROOT" "$RUNTIME_ROOT"
      launchctl bootstrap "$AGENT_DOMAIN" "$AGENT_PLIST" || true
    fi
    rm -rf "$FAILED_ROOT"
    printf '%s\n' 'The updated Sync agent did not start; the previous runtimes were restored.' >&2
    exit 1
  fi
fi

rm -rf "$BACKUP_ROOT"
trap - EXIT HUP INT TERM

printf '%s\n' "$RUNTIME_ROOT/sync/bin/personal-agent-sync"
printf '%s\n' "$RUNTIME_ROOT/corpus/bin/python"
