#!/usr/bin/env python3
"""Archive calendar events to a persistent rolling history.

Reads today's calendar JSON from 00-Inbox/ and appends events to
_db/calendar-history.json with dedup and 7-day pruning.

Usage:
  python archive-calendar.py --vault /path/to/vault
  python archive-calendar.py --vault /path/to/vault --quiet
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Archive calendar events to persistent history")
    parser.add_argument("--vault", required=True, help="Path to vault root")
    parser.add_argument("--quiet", action="store_true", help="Suppress output")
    parser.add_argument("--retention-days", type=int, default=7, help="Days to keep (default 7)")
    args = parser.parse_args()

    vault = Path(args.vault)
    inbox = vault / "00-Inbox"
    history_path = vault / "_db" / "calendar-history.json"

    def log(msg):
        if not args.quiet:
            print(msg)

    # Find calendar JSON files in inbox
    cal_files = sorted(inbox.glob("*-calendar.json"))
    if not cal_files:
        log("No calendar JSON found in inbox, skipping")
        return

    # Load existing history
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            history = {"events": []}
    else:
        history = {"events": []}

    existing_events = history.get("events", [])

    # Build dedup set from existing events
    def event_key(e):
        return (e.get("subject", ""), e.get("start", ""), e.get("end", ""), e.get("organizer", ""))

    seen = {event_key(e) for e in existing_events}
    added = 0

    # Process each calendar file
    for cal_file in cal_files:
        # Extract date from filename (YYYY-MM-DD-calendar.json)
        date_pulled = cal_file.stem.replace("-calendar", "")

        try:
            events = json.loads(cal_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARNING: Failed to read {cal_file.name}: {e}", file=sys.stderr)
            continue

        if not isinstance(events, list):
            continue

        for event in events:
            key = event_key(event)
            if key in seen:
                continue
            seen.add(key)
            event["date_pulled"] = date_pulled
            existing_events.append(event)
            added += 1

    # Prune events older than retention window
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=args.retention_days)).isoformat()
    before_prune = len(existing_events)
    existing_events = [
        e for e in existing_events
        if e.get("start", "") >= cutoff[:10] or e.get("date_pulled", "") >= cutoff[:10]
    ]
    pruned = before_prune - len(existing_events)

    # Write back
    history["events"] = existing_events
    history["last_updated"] = datetime.now(tz=timezone.utc).isoformat()
    history_path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")

    log(f"Calendar history: {added} added, {pruned} pruned, {len(existing_events)} total")


if __name__ == "__main__":
    main()
