#!/bin/sh
set -eu
umask 077

if [ "$#" -ne 1 ]; then
  echo "usage: backup.sh /absolute/path/to/journal-backup.sql" >&2
  exit 2
fi

OUTPUT=$1
case "$OUTPUT" in
  /*) ;;
  *) echo "backup output must be an absolute path" >&2; exit 2 ;;
esac

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SERVICE_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
mkdir -p "$(dirname -- "$OUTPUT")"
cd "$SERVICE_DIR"
npx wrangler d1 export personal-agent-journal --remote --output "$OUTPUT"
chmod 600 "$OUTPUT"
