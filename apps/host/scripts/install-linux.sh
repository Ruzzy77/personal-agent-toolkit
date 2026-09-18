#!/usr/bin/env bash
# Bootstrap the Personal Agent Host prefix on a Linux host (arm64 or x86_64):
# uv, the host and corpus runtimes, cloudflared, the launcher, and the sandbox image.
# Re-running upgrades in place. Run `personal-agent-host install` afterwards.
set -euo pipefail

PREFIX="${PERSONAL_AGENT_HOST_PREFIX:-$HOME/.local/share/personal-agent-host}"
REPO="${1:-$(cd "$(dirname "$0")/../../.." && pwd)}"
CLOUDFLARED_VERSION="${CLOUDFLARED_VERSION:-2026.9.1}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"

case "$(uname -m)" in
  aarch64|arm64) CF_ARCH=arm64 ;;
  x86_64) CF_ARCH=amd64 ;;
  *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

for dir in bin runtimes config state jobs logs; do
  mkdir -p "$PREFIX/$dir"
done
chmod 700 "$PREFIX/config" "$PREFIX/state" "$PREFIX/jobs"

if [ ! -x "$PREFIX/bin/uv" ]; then
  curl -fsSL https://astral.sh/uv/install.sh | UV_INSTALL_DIR="$PREFIX/bin" UV_NO_MODIFY_PATH=1 sh
fi
UV="$PREFIX/bin/uv"

"$UV" venv --quiet --python "$PYTHON_VERSION" "$PREFIX/runtimes/host"
"$UV" pip install --quiet --python "$PREFIX/runtimes/host/bin/python" \
  "$REPO/apps/host" "$REPO/apps/sync" "$REPO/plugins/document-files"
"$UV" venv --quiet --python "$PYTHON_VERSION" "$PREFIX/runtimes/corpus"
"$UV" pip install --quiet --python "$PREFIX/runtimes/corpus/bin/python" "$REPO/engines/corpus"
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
