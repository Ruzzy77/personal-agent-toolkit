#!/bin/sh
set -eu

if [ "$#" -lt 1 ] || [ "$#" -gt 3 ]; then
  echo "usage: restore.sh BACKUP.sql [DATABASE_OR_BINDING] [--local|--remote]" >&2
  exit 2
fi

INPUT=$1
DATABASE=${2:-DB}
SCOPE=${3:---local}

if [ ! -f "$INPUT" ]; then
  echo "backup file does not exist: $INPUT" >&2
  exit 2
fi
if [ "$SCOPE" != "--local" ] && [ "$SCOPE" != "--remote" ]; then
  echo "scope must be --local or --remote" >&2
  exit 2
fi
if [ "$SCOPE" = "--remote" ] && [ "${JOURNAL_RESTORE_CONFIRM:-}" != "restore:$DATABASE" ]; then
  echo "remote restore requires JOURNAL_RESTORE_CONFIRM=restore:$DATABASE" >&2
  exit 2
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SERVICE_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$SERVICE_DIR"
if [ "$SCOPE" = "--local" ] && [ -n "${JOURNAL_D1_PERSIST_TO:-}" ]; then
  npx wrangler d1 execute "$DATABASE" --local \
    --persist-to "$JOURNAL_D1_PERSIST_TO" --file "$INPUT"
else
  npx wrangler d1 execute "$DATABASE" "$SCOPE" --file "$INPUT"
fi
