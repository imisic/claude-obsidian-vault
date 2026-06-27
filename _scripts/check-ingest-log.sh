#!/usr/bin/env bash
# check-ingest-log.sh: Remove ghost entries from ingest-log.json
# Ghost = action:"created" but output-file doesn't exist on disk
# Usage: ./check-ingest-log.sh [--if-stale] [vault-root]
#   --if-stale  Skip if last audit was within 7 days

set -euo pipefail

IF_STALE=false
POSITIONAL=()
for arg in "$@"; do
  case "$arg" in
    --if-stale) IF_STALE=true ;;
    *) POSITIONAL+=("$arg") ;;
  esac
done

VAULT="${POSITIONAL[0]:-~/Obsidian/Vault}"
LOG="$VAULT/_db/ingest-log.json"
LAST_AUDIT="$VAULT/_db/.last-audit"

# --if-stale: skip if last audit is within 7 days
if [[ "$IF_STALE" == true && -f "$LAST_AUDIT" ]]; then
  last_date=$(cat "$LAST_AUDIT" | tr -d '[:space:]')
  cutoff=$(date -d "7 days ago" +%Y-%m-%d)
  if [[ "$last_date" > "$cutoff" || "$last_date" == "$cutoff" ]]; then
    echo "Audit skipped (last: $last_date)"
    exit 0
  fi
fi

if [[ ! -f "$LOG" ]]; then
  echo '[]' > "$LOG"
  echo "Created empty ingest-log.json"
  [[ "$IF_STALE" == true ]] && date +%Y-%m-%d > "$LAST_AUDIT"
  exit 0
fi

TMPFILE=$(mktemp)
trap 'rm -f "$TMPFILE"' EXIT

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPT_DIR/_check_ingest_log_impl.py" "$VAULT" "$LOG" "$TMPFILE"

mv "$TMPFILE" "$LOG"
trap - EXIT

# Update last-audit timestamp on successful run
[[ "$IF_STALE" == true ]] && date +%Y-%m-%d > "$LAST_AUDIT"
