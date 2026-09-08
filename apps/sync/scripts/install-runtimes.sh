#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../../.." && pwd)
DEFAULT_RUNTIME_ROOT="$HOME/Library/Application Support/Personal Agent Sync/runtimes"
LINK_CLI_ONLY=0
if [ "${1:-}" = "--link-cli-only" ]; then
  LINK_CLI_ONLY=1
  shift
  [ "$#" -eq 0 ] || {
    printf '%s\n' '--link-cli-only accepts no runtime path.' >&2
    exit 1
  }
fi
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

# The user PATH entry is an absent-only link to the durable default runtime.
# Custom runtime installations must not replace the host's registered command.
link_default_cli() {
  "$DEFAULT_RUNTIME_ROOT/sync/bin/python" - "$DEFAULT_RUNTIME_ROOT" <<'PYLINK'
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
target = root / "sync/bin/personal-agent-sync"
link = Path.home() / ".local/bin/personal-agent-sync"
if not target.is_file() or not os.access(target, os.X_OK):
    raise SystemExit("The installed Sync executable is unavailable; no PATH link was changed.")
if os.path.lexists(link):
    if not link.is_symlink() or os.readlink(link) != str(target):
        raise SystemExit("The PATH entry already belongs to another executable; it was not changed.")
else:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, link)
    except FileExistsError:
        raise SystemExit("The PATH entry changed during setup; it was not replaced.") from None
print(link)
PYLINK
}

if [ "$LINK_CLI_ONLY" -eq 1 ]; then
  umask 077
  link_default_cli
  exit 0
fi

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
"$sync_destination/bin/python" "$REPOSITORY_ROOT/scripts/check_repository.py" --document-files-only
release_state=$("$sync_destination/bin/python" - "$REPOSITORY_ROOT" <<'PYCODE'
import sys
sys.path.insert(0, sys.argv[1] + "/scripts")
from document_files_release import release_lock
print(release_lock()["state"])
PYCODE
)
if [ "$release_state" = "pinned" ]; then
  # Preparation is explicit. Neither this resolver nor document processing downloads a backend.
  document_wheel=$("$sync_destination/bin/python" "$REPOSITORY_ROOT/scripts/document_files_release.py" wheel)
  (cd "$REPOSITORY_ROOT/apps/sync" && uv export --frozen --no-emit-project \
    --no-emit-package document-files --format requirements-txt \
    --output-file "$STAGING_ROOT/sync-dependencies.txt")
  uv pip install --python "$sync_destination/bin/python" --require-hashes \
    -r "$STAGING_ROOT/sync-dependencies.txt"
  uv pip install --python "$sync_destination/bin/python" --no-deps \
    "$document_wheel" "$REPOSITORY_ROOT/apps/sync"
  rm "$STAGING_ROOT/sync-dependencies.txt"
  "$sync_destination/bin/python" - "$REPOSITORY_ROOT" "$sync_destination" <<'PYCODE'
import hashlib
import json
import platform
import sys
import zipfile
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/scripts")
from document_files_release import artifact_path, release_lock
root = Path(sys.argv[2])
machine = platform.machine().lower()
target = "macos-aarch64" if machine in {"aarch64", "arm64"} else "macos-x86_64"
name = "runtime-" + target
lock = release_lock()
if name not in lock.get("artifacts", {}):
    print("No pinned rhwp runtime provided; no backend will be provisioned.")
else:
    archive = artifact_path(name)
    with zipfile.ZipFile(archive) as bundle:
        members = {name: bundle.getinfo("document-files/rhwp/" + name)
                   for name in ("rhwp", "LICENSE", "build.json")}
        if any(item.file_size > 128 * 1024 * 1024 for item in members.values()):
            raise ValueError("Pinned rhwp files exceed installation budget")
        binary = bundle.read(members["rhwp"])
        metadata = json.loads(bundle.read(members["build.json"]))
        if (metadata.get("version") != "0.8.6+pat.checkbox.1" or
                metadata.get("binarySha256") != hashlib.sha256(binary).hexdigest()):
            raise ValueError("Pinned rhwp binary metadata is invalid")
        (root / "bin/rhwp").write_bytes(binary)
        (root / "bin/rhwp").chmod(0o755)
        notices = root / "share/document-files/rhwp"
        notices.mkdir(parents=True, exist_ok=True)
        for name in ("LICENSE", "build.json"):
            (notices / name).write_bytes(bundle.read(members[name]))
    # Resolve the bundled binary even when launchd's PATH omits the venv bin directory.
    entry = root / "bin/personal-agent-sync"
    entry.write_text('#!/bin/sh\nset -eu\n'
                     'BIN=$(CDPATH= cd -- "$(dirname -- "$(realpath -- "$0")")" && pwd)\n'
                     'export DOCUMENT_FILES_RHWP=${DOCUMENT_FILES_RHWP:-"$BIN/rhwp"}\n'
                     'exec "$BIN/python" -m personal_agent_sync.cli "$@"\n')
    entry.chmod(0o755)
PYCODE
else
  uv pip install --python "$sync_destination/bin/python" \
    "$REPOSITORY_ROOT/plugins/document-files" "$REPOSITORY_ROOT/apps/sync"
  "$sync_destination/bin/python" \
    "$REPOSITORY_ROOT/plugins/document-files/scripts/provision_rhwp.py" >/dev/null
fi
install_runtime corpus "$REPOSITORY_ROOT/engines/corpus"

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

if [ "$RUNTIME_ROOT" = "$DEFAULT_RUNTIME_ROOT" ]; then
  link_default_cli
fi

rm -rf "$BACKUP_ROOT"
trap - EXIT HUP INT TERM

printf '%s\n' "$RUNTIME_ROOT/sync/bin/personal-agent-sync"
printf '%s\n' "$RUNTIME_ROOT/corpus/bin/python"
