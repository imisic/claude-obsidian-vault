#!/usr/bin/env python3
"""Helper for check-ingest-log.sh, removes ghost entries and deduplicates."""
import json, os, sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import atomic_json_write

vault = sys.argv[1]
log_path = sys.argv[2]
out_path = sys.argv[3]

with open(log_path) as f:
    log = json.load(f)

original_count = len(log)

# Step 1: Deduplicate by source-file (keep latest "created", prefer "created" over "skipped")
seen = {}
for entry in log:
    sf = entry.get("source-file", "")
    if not sf:
        continue
    action = entry.get("action", "")
    if sf in seen:
        existing = seen[sf]
        # Both "created" → keep first (original processing)
        if existing["action"] == "created" and action == "created":
            continue
        # "skipped" then "created" → keep "created" (re-evaluation upgraded)
        if existing["action"].startswith("skipped") and action == "created":
            seen[sf] = entry
            continue
        # "created" then "skipped" → keep "created"
        if existing["action"] == "created" and action.startswith("skipped"):
            continue
        # Same type → keep first
        continue
    seen[sf] = entry

deduped = list(seen.values())
dedup_removed = original_count - len(deduped)

# Step 2: Remove ghost entries (action:"created" but output-file missing)
ghost_removed = []
kept = []
for entry in deduped:
    if entry.get("action") == "created" and entry.get("output-file"):
        full_path = os.path.join(vault, entry["output-file"])
        if not os.path.exists(full_path):
            ghost_removed.append(entry.get("source-file", "?"))
            continue
    kept.append(entry)

# Step 3: Rotate old entries (keep last 90 days in active log, archive older)
cutoff = (datetime.now() - timedelta(days=90)).isoformat()
current_year = datetime.now().strftime("%Y")
archive_entries = [e for e in kept if e.get("timestamp", "9999") < cutoff]
active_entries = [e for e in kept if e.get("timestamp", "9999") >= cutoff]

if archive_entries:
    archive_path = Path(vault) / "_db" / f"ingest-log-archive-{current_year}.json"
    existing_archive = []
    if archive_path.exists():
        with open(archive_path) as f:
            existing_archive = json.load(f)
    existing_archive.extend(archive_entries)
    atomic_json_write(archive_path, existing_archive)

# Sort by timestamp
active_entries.sort(key=lambda e: e.get("timestamp", ""))

atomic_json_write(Path(out_path), active_entries)

# Report
parts = []
if dedup_removed:
    parts.append(f"deduped {dedup_removed}")
if ghost_removed:
    parts.append(f"removed {len(ghost_removed)} ghosts: {', '.join(ghost_removed)}")
if archive_entries:
    parts.append(f"archived {len(archive_entries)} old entries")
if parts:
    print(f"Ingest-log cleanup: {'; '.join(parts)} ({len(active_entries)} active)")
else:
    print(f"Ingest-log clean ({len(active_entries)} entries)")
