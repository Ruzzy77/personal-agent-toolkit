#!/bin/bash
set -euo pipefail
IFS=$'\n\t'
umask 077

MARKETPLACE_NAME="personal-agent-toolkit"
PLUGIN_IDS=(
  "sense@personal-agent-toolkit"
  "corpus@personal-agent-toolkit"
  "hypes@personal-agent-toolkit"
)
DEFAULT_GATEWAY_PLIST="$HOME/Library/LaunchAgents/com.ruzzy77.personal-agent-tunnel.gateway.plist"
REPLACE_MARKETPLACE=0
SKIP_TESTS=0
SKIP_GATEWAY=0
GATEWAY_PLIST="$DEFAULT_GATEWAY_PLIST"

usage() {
  cat <<'EOF'
Usage: bash scripts/reinstall_corpus_macos.sh [options]

Reinstall this checkout's local Corpus package and refresh the personal ChatGPT gateway.

Options:
  --replace-marketplace  Replace an existing non-local personal-agent-toolkit marketplace.
                         This temporarily uninstalls its plugins, then reinstalls all three.
  --skip-tests           Skip the bounded-read unit test and Ruff checks.
  --skip-gateway         Do not rebuild or restart the existing gateway LaunchAgent.
  --gateway-plist PATH   Use a non-default gateway LaunchAgent plist.
  -h, --help             Show this help.
EOF
}

while (($#)); do
  case "$1" in
    --replace-marketplace)
      REPLACE_MARKETPLACE=1
      ;;
    --skip-tests)
      SKIP_TESTS=1
      ;;
    --skip-gateway)
      SKIP_GATEWAY=1
      ;;
    --gateway-plist)
      shift
      if (($# == 0)); then
        echo "--gateway-plist requires a path" >&2
        exit 64
      fi
      GATEWAY_PLIST="$1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 64
      ;;
  esac
  shift
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer must run on macOS." >&2
  exit 1
fi

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
CORPUS_ROOT="$REPO_ROOT/plugins/corpus"

for command in python3 uv codex; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "required command not found: $command" >&2
    exit 127
  fi
done

for required in \
  "$CORPUS_ROOT/pyproject.toml" \
  "$CORPUS_ROOT/.codex-plugin/plugin.json" \
  "$CORPUS_ROOT/src/corpus/mcp_server_bounded.py" \
  "$CORPUS_ROOT/launchers/corpus-mcp" \
  "$REPO_ROOT/gateway/launchers/personal-agent-tunnel-service" \
  "$REPO_ROOT/gateway/launchers/personal-agent-tunnel-gateway"; do
  if [[ ! -f "$required" ]]; then
    echo "checkout is incomplete: $required" >&2
    exit 1
  fi
done

EXPECTED_BUILD="$({
  python3 - "$CORPUS_ROOT/.codex-plugin/plugin.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
version = payload.get("version")
if not isinstance(version, str) or not version:
    raise SystemExit("Corpus plugin manifest has no version")
print(version)
PY
} 2>&1)" || {
  echo "$EXPECTED_BUILD" >&2
  exit 1
}

echo "Corpus build: $EXPECTED_BUILD"

if ((SKIP_TESTS == 0)); then
  echo "Running Corpus regression tests..."
  (
    cd "$REPO_ROOT"
    uv run --project "$CORPUS_ROOT" --frozen \
      python -m unittest discover \
      -s "$CORPUS_ROOT/tests" \
      -p 'test_*.py'
    uv run --project "$CORPUS_ROOT" --frozen \
      ruff check \
      "$CORPUS_ROOT/src/corpus/mcp_server_bounded.py" \
      "$CORPUS_ROOT/tests/test_mcp_space_file_read.py"
  )
fi

codex_plugin_is_this_checkout() {
  local listing
  if ! listing="$(codex plugin list --json)"; then
    return 1
  fi
  PLUGIN_LIST_JSON="$listing" \
  REPO_ROOT_VALUE="$REPO_ROOT" \
  EXPECTED_BUILD_VALUE="$EXPECTED_BUILD" \
  python3 - <<'PY'
import json
import os
from pathlib import Path

payload = json.loads(os.environ["PLUGIN_LIST_JSON"])
repo = Path(os.environ["REPO_ROOT_VALUE"]).resolve()
expected_build = os.environ["EXPECTED_BUILD_VALUE"]
allowed_roots = {repo, (repo / "plugins/corpus").resolve()}


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)

for item in walk(payload):
    plugin_id = item.get("pluginId") or item.get("plugin_id")
    name = item.get("name")
    if plugin_id != "corpus@personal-agent-toolkit" and name != "corpus":
        continue
    if item.get("installed") is False or item.get("enabled") is False:
        continue
    source = item.get("source")
    if not isinstance(source, dict) or source.get("source") != "local":
        continue
    raw_path = source.get("path")
    if not isinstance(raw_path, str):
        continue
    try:
        source_path = Path(raw_path).expanduser().resolve()
    except OSError:
        continue
    if source_path not in allowed_roots:
        continue
    reported_versions = {
        value
        for key in ("version", "installedVersion", "installed_version")
        if isinstance((value := item.get(key)), str)
    }
    if reported_versions and expected_build not in reported_versions:
        continue
    raise SystemExit(0)
raise SystemExit(1)
PY
}

marketplace_add_output="$(mktemp -t corpus-marketplace-add.XXXXXX)"
trap 'rm -f "$marketplace_add_output"' EXIT

if codex plugin marketplace add "$REPO_ROOT" >"$marketplace_add_output" 2>&1; then
  echo "Registered this checkout as the local Codex marketplace."
elif codex_plugin_is_this_checkout; then
  echo "The local Codex marketplace already points to this checkout."
elif ((REPLACE_MARKETPLACE == 1)); then
  echo "Replacing the existing $MARKETPLACE_NAME marketplace..."
  codex plugin marketplace remove "$MARKETPLACE_NAME" || true
  codex plugin marketplace add "$REPO_ROOT"
else
  cat "$marketplace_add_output" >&2
  cat >&2 <<EOF
The existing $MARKETPLACE_NAME registration does not point to this checkout.
Rerun with --replace-marketplace to remove it and install this local checkout.
EOF
  exit 1
fi

for plugin_id in "${PLUGIN_IDS[@]}"; do
  echo "Installing $plugin_id..."
  codex plugin add "$plugin_id"
done

if ! codex_plugin_is_this_checkout; then
  echo "Codex did not report an enabled local Corpus installation from this checkout." >&2
  exit 1
fi

if ((SKIP_GATEWAY == 0)); then
  if [[ -f "$GATEWAY_PLIST" ]]; then
    echo "Refreshing the existing personal ChatGPT gateway..."
    python3 - "$GATEWAY_PLIST" "$REPO_ROOT" <<'PY'
from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

plist_path = Path(sys.argv[1]).expanduser().resolve()
repo_root = Path(sys.argv[2]).resolve()
service_program = repo_root / "gateway/launchers/personal-agent-tunnel-service"
gateway_program = repo_root / "gateway/launchers/personal-agent-tunnel-gateway"

with plist_path.open("rb") as handle:
    old_payload = plistlib.load(handle)
arguments = old_payload.get("ProgramArguments")
if not isinstance(arguments, list) or not all(isinstance(value, str) for value in arguments):
    raise SystemExit("gateway plist has invalid ProgramArguments")


def one(flag: str, default: str | None = None) -> str:
    positions = [index for index, value in enumerate(arguments) if value == flag]
    if not positions:
        if default is not None:
            return default
        raise SystemExit(f"gateway plist is missing {flag}")
    index = positions[-1]
    if index + 1 >= len(arguments):
        raise SystemExit(f"gateway plist has no value for {flag}")
    return arguments[index + 1]


def many(flag: str) -> list[str]:
    values: list[str] = []
    for index, value in enumerate(arguments):
        if value == flag:
            if index + 1 >= len(arguments):
                raise SystemExit(f"gateway plist has no value for {flag}")
            values.append(arguments[index + 1])
    return values

products = many("--product")
if not products:
    raise SystemExit("gateway plist has no selected products")

tunnel_client = one("--tunnel-client")
keychain_service = one("--keychain-service", "personal-agent-tunnel-control-plane")
keychain_account = one("--keychain-account", "user")
host = one("--host", "127.0.0.1")
port = int(one("--port", "18180"))

command = [
    str(service_program),
    "install-gateway-launch-agent",
    "--runtime-program",
    str(service_program),
    "--gateway-program",
    str(gateway_program),
    "--tunnel-client",
    tunnel_client,
    "--keychain-service",
    keychain_service,
    "--keychain-account",
    keychain_account,
    "--host",
    host,
    "--port",
    str(port),
]
for product in products:
    command.extend(("--product", product))

environment = old_payload.get("EnvironmentVariables")
if isinstance(environment, dict):
    uv_program = environment.get("UV")
    if isinstance(uv_program, str) and uv_program:
        command.extend(("--uv-program", uv_program))

subprocess.run(command, check=True)

label = "com.ruzzy77.personal-agent-tunnel.gateway"
domain = f"gui/{os.getuid()}"
subprocess.run(
    ["launchctl", "bootout", domain, str(plist_path)],
    check=False,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], check=True)
subprocess.run(["launchctl", "kickstart", "-k", f"{domain}/{label}"], check=True)

url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
health_url = f"http://{url_host}:{port}/healthz"
deadline = time.monotonic() + 150.0
last_error: Exception | None = None
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(health_url, timeout=2.0) as response:
            if response.status == 200:
                health = json.load(response)
                if health.get("ok") is True:
                    break
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        last_error = exc
    time.sleep(0.5)
else:
    raise SystemExit(f"gateway did not become healthy: {last_error}")

if "corpus" in products:
    request = urllib.request.Request(
        "http://127.0.0.1:18182/mcp",
        data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}',
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10.0) as response:
        tool_payload = json.load(response)
    tools = tool_payload.get("result", {}).get("tools")
    if not isinstance(tools, list):
        raise SystemExit("Corpus MCP returned an invalid tools/list response")
    observed = {tool.get("name") for tool in tools if isinstance(tool, dict)}
    expected = {
        "corpus_space_list",
        "corpus_space_get",
        "corpus_space_search",
        "corpus_file_list",
        "corpus_file_read",
        "corpus_file_write",
        "corpus_file_select_current",
        "corpus_file_restore",
    }
    if observed != expected:
        raise SystemExit(
            "Corpus MCP tool surface mismatch: "
            f"missing={sorted(expected - observed)}, extra={sorted(observed - expected)}"
        )

print(
    json.dumps(
        {
            "gateway": "healthy",
            "health_url": health_url,
            "products": products,
            "corpus_tools_verified": "corpus" in products,
        },
        ensure_ascii=False,
    )
)
PY
  else
    echo "No gateway LaunchAgent found at $GATEWAY_PLIST; Codex reinstall completed without gateway changes."
  fi
fi

cat <<EOF

Corpus reinstall completed.
Installed build: $EXPECTED_BUILD
Start a new Chat or Codex task before testing; the current host keeps its previously loaded tool snapshot.
For the bounded-read check, call corpus_file_read on a UTF-8 Work file with max_chars=1000 and require:
  returned_chars=1000, total_chars>=returned_chars, and truncated=true when the file is longer.
EOF
