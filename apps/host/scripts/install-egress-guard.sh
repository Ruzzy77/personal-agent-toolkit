#!/usr/bin/bash
# Install the fixed Personal Agent Host egress guard and its narrow sudo rule.
set -euo pipefail

if [ "$(/usr/bin/id -u)" -ne 0 ]; then
  echo "run as root" >&2
  exit 1
fi
if [ "$#" -ne 2 ]; then
  echo "usage: install-egress-guard.sh <repository> <host-service-user>" >&2
  exit 2
fi

REPO="$(cd "$1" && /bin/pwd)"
SERVICE_USER="$2"
SOURCE="$REPO/apps/host/scripts/egress-guard.py"
TARGET="/usr/local/libexec/personal-agent-host-egress-guard"
SUDOERS="/etc/sudoers.d/personal-agent-host-egress-guard"
VISUDO="/usr/sbin/visudo"

# These absolute paths are also compiled into the helper. Refuse installation
# rather than letting the service account's PATH choose a privileged command.
for executable in /usr/bin/python3 /usr/bin/docker /usr/sbin/ip /usr/sbin/iptables "$VISUDO"; do
  [ -x "$executable" ] || { echo "$executable is missing" >&2; exit 1; }
done
/usr/bin/id "$SERVICE_USER" >/dev/null
[ -f "$SOURCE" ] || { echo "$SOURCE is missing" >&2; exit 1; }

/usr/bin/install -d -o root -g root -m 0755 /usr/local/libexec
/usr/bin/install -o root -g root -m 0755 "$SOURCE" "$TARGET"

temporary="$(/usr/bin/mktemp)"
trap '/bin/rm -f "$temporary"' EXIT
/usr/bin/cat >"$temporary" <<EOF
# Personal Agent Host 0.4 fixed egress guard. The root-owned helper validates
# action, exact pah-egress-<12hex> name and Host-owned Docker network label.
Defaults!$TARGET env_reset,secure_path=/usr/sbin:/usr/bin:/sbin:/bin
$SERVICE_USER ALL=(root) NOPASSWD: $TARGET attach pah-egress-*
$SERVICE_USER ALL=(root) NOPASSWD: $TARGET check pah-egress-*
$SERVICE_USER ALL=(root) NOPASSWD: $TARGET detach pah-egress-*
EOF
/bin/chmod 0440 "$temporary"
"$VISUDO" -cf "$temporary" >/dev/null
/usr/bin/install -o root -g root -m 0440 "$temporary" "$SUDOERS"
"$VISUDO" -cf "$SUDOERS" >/dev/null

test "$(/usr/bin/stat -c '%U:%G:%a' "$TARGET")" = "root:root:755"
test "$(/usr/bin/stat -c '%U:%G:%a' "$SUDOERS")" = "root:root:440"
echo "PAT_EGRESS_GUARD_READY"
