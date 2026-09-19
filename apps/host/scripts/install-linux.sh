#!/usr/bin/env bash
# Bootstrap the Personal Agent Host prefix on a Linux host (arm64 or x86_64):
# uv, the host and corpus runtimes, cloudflared, the launcher, and the sandbox image.
# Re-running upgrades in place. Run `personal-agent-host install` afterwards.
#
# --runtime-only reinstalls selected packages into the existing runtimes and
# leaves uv, Python, cloudflared, the sandbox image, systemd units, the config
# and the state alone. Stop the Host service first: the branch refuses to touch
# a running installation.
#
#   install-linux.sh <repo> --runtime-only [--packages host,sync,corpus,document-files] [--no-deps]
#
# --packages defaults to host,sync. Add --no-deps for a code-only update whose
# dependencies are unchanged.
set -euo pipefail

PREFIX="${PERSONAL_AGENT_HOST_PREFIX:-$HOME/.local/share/personal-agent-host}"
CLOUDFLARED_VERSION="${CLOUDFLARED_VERSION:-2026.9.1}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
RUNTIME_ONLY=0
PACKAGES="host,sync"
NO_DEPS=0
REPO=""

while [ $# -gt 0 ]; do
  case "$1" in
    --runtime-only) RUNTIME_ONLY=1 ;;
    --packages) PACKAGES="${2:-}"; shift ;;
    --packages=*) PACKAGES="${1#*=}" ;;
    --no-deps) NO_DEPS=1 ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) REPO="$1" ;;
  esac
  shift
done
REPO="${REPO:-$(cd "$(dirname "$0")/../../.." && pwd)}"

case "$(uname -m)" in
  aarch64|arm64) CF_ARCH=arm64 ;;
  x86_64) CF_ARCH=amd64 ;;
  *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

HOST_PYTHON="$PREFIX/runtimes/host/bin/python"
CORPUS_PYTHON="$PREFIX/runtimes/corpus/bin/python"

if [ "$RUNTIME_ONLY" = 1 ]; then
  UV="$PREFIX/bin/uv"
  [ -x "$UV" ] || { echo "$UV is missing; run the full install first" >&2; exit 1; }
  for python in "$HOST_PYTHON" "$CORPUS_PYTHON"; do
    [ -x "$python" ] || { echo "$python is missing; run the full install first" >&2; exit 1; }
  done
  if command -v systemctl >/dev/null 2>&1; then
    for unit in personal-agent-host.service personal-agent-backup.service; do
      if [ "$(systemctl --user is-active "$unit" 2>/dev/null || true)" = "active" ]; then
        echo "$unit is running; stop it before updating the runtime" >&2
        exit 1
      fi
    done
  fi

  host_names=(); host_targets=(); corpus_names=(); corpus_targets=()
  IFS=',' read -r -a selected <<<"$PACKAGES"
  for name in "${selected[@]}"; do
    case "$name" in
      host) host_names+=("personal-agent-host"); host_targets+=("$REPO/apps/host") ;;
      sync) host_names+=("personal-agent-sync"); host_targets+=("$REPO/apps/sync") ;;
      document-files)
        host_names+=("document-files"); host_targets+=("$REPO/plugins/document-files") ;;
      corpus) corpus_names+=("corpus"); corpus_targets+=("$REPO/engines/corpus") ;;
      "") ;;
      *) echo "unknown package: $name" >&2; exit 2 ;;
    esac
  done
  if [ ${#host_targets[@]} -eq 0 ] && [ ${#corpus_targets[@]} -eq 0 ]; then
    echo "no packages selected" >&2
    exit 2
  fi

  reinstall() {
    local python="$1" list="$2" paths="$3"
    local -a args=(pip install --python "$python")
    [ "$NO_DEPS" = 1 ] && args+=(--no-deps)
    local name
    for name in $list; do
      "$UV" cache clean "$name" >/dev/null 2>&1 || true
      args+=(--reinstall-package "$name")
    done
    local path
    for path in $paths; do
      [ -d "$path" ] || { echo "$path is missing in $REPO" >&2; exit 1; }
      args+=("$path")
    done
    "$UV" "${args[@]}"
    "$UV" pip check --python "$python"
  }

  if [ ${#host_targets[@]} -gt 0 ]; then
    reinstall "$HOST_PYTHON" "${host_names[*]}" "${host_targets[*]}"
  fi
  if [ ${#corpus_targets[@]} -gt 0 ]; then
    reinstall "$CORPUS_PYTHON" "${corpus_names[*]}" "${corpus_targets[*]}"
  fi

  echo "runtime updated from $REPO: $PACKAGES"
  echo "next: start personal-agent-host.service and restore the backup timer"
  exit 0
fi

for dir in bin runtimes config state jobs logs; do
  mkdir -p "$PREFIX/$dir"
done
chmod 700 "$PREFIX/config" "$PREFIX/state" "$PREFIX/jobs"

if [ ! -x "$PREFIX/bin/uv" ]; then
  curl -fsSL https://astral.sh/uv/install.sh | UV_INSTALL_DIR="$PREFIX/bin" UV_NO_MODIFY_PATH=1 sh
fi
UV="$PREFIX/bin/uv"

"$UV" venv --quiet --python "$PYTHON_VERSION" "$PREFIX/runtimes/host"
"$UV" pip install --quiet --python "$HOST_PYTHON" \
  "$REPO/apps/host" "$REPO/apps/sync" "$REPO/plugins/document-files"
"$UV" venv --quiet --python "$PYTHON_VERSION" "$PREFIX/runtimes/corpus"
"$UV" pip install --quiet --python "$CORPUS_PYTHON" "$REPO/engines/corpus"
ln -sfn "$PREFIX/runtimes/host/bin/personal-agent-host" "$PREFIX/bin/personal-agent-host"
ln -sfn "$PREFIX/runtimes/host/bin/personal-agent-sync" "$PREFIX/bin/personal-agent-sync"

if [ ! -x "$PREFIX/bin/cloudflared" ] || ! "$PREFIX/bin/cloudflared" --version | grep -q "$CLOUDFLARED_VERSION"; then
  curl -fsSL -o "$PREFIX/bin/cloudflared.new" \
    "https://github.com/cloudflare/cloudflared/releases/download/$CLOUDFLARED_VERSION/cloudflared-linux-$CF_ARCH"
  chmod 755 "$PREFIX/bin/cloudflared.new"
  mv "$PREFIX/bin/cloudflared.new" "$PREFIX/bin/cloudflared"
fi

if command -v docker >/dev/null 2>&1; then
  docker build --quiet -t personal-agent-host-sandbox:1 "$REPO/plugins/host/sandbox" >/dev/null
fi

if [ ! -s "$PREFIX/config/host-upstream.token" ]; then
  umask 077
  head -c 32 /dev/urandom | base64 | tr -d '=+/\n' > "$PREFIX/config/host-upstream.token"
  echo "generated $PREFIX/config/host-upstream.token; set it as HOST_UPSTREAM_TOKEN on the Worker"
fi

echo "prefix ready: $PREFIX"
echo "next: write $PREFIX/config/host.toml, put the tunnel token in $PREFIX/config/tunnel.token, then run $PREFIX/bin/personal-agent-host install"
