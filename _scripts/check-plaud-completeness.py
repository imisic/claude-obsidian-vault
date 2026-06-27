#!/usr/bin/env python3
"""Compare Plaud API recordings for a given date against what's been pulled locally.

Prints a warning if the API has recordings for the date that aren't found
locally (either in 00-Inbox/ or _attachments/). Exits 0 always. This is a
soft check meant to surface drift, not block the pipeline.

Usage:
  python check-plaud-completeness.py --vault PATH --date YYYY-MM-DD
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from plaud_api import load_plaud_auth, PlaudClient


def ensure_utf8_stdio():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main():
    ensure_utf8_stdio()
    p = argparse.ArgumentParser()
    p.add_argument("--vault", required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    vault = Path(args.vault)
    target = args.date

    auth = load_plaud_auth()
    if auth is None:
        if not args.quiet:
            print("Plaud: no auth configured, skipping completeness check", file=sys.stderr)
        return 0

    try:
        try:
            day_start = datetime.fromisoformat(target).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            print(f"check-plaud-completeness: bad date {target}", file=sys.stderr)
            return 0
        day_end = day_start + 86400

        files = PlaudClient(auth).list_all_files()
    except Exception as exc:
        if not args.quiet:
            print(f"check-plaud-completeness: API error: {exc}", file=sys.stderr)
        return 0

    api_today = []
    for f in files:
        if not isinstance(f, dict):
            continue
        if f.get("is_trash") or not f.get("is_trans"):
            continue
        start = f.get("start_time")
        if not isinstance(start, (int, float)):
            continue
        start_s = start / 1000.0 if start > 10**12 else start
        if day_start <= start_s < day_end:
            api_today.append({
                "subject": f.get("filename", ""),
                "start": start_s,
            })

    inbox = vault / "00-Inbox"
    attach = vault / "_attachments"
    local_count = (
        len(list(inbox.glob(f"transcript-plaud-{target}-*.txt"))) +
        len(list(attach.glob(f"transcript-plaud-{target}-*.txt")))
    )

    api_count = len(api_today)
    if api_count == local_count:
        if not args.quiet:
            print(f"Plaud completeness OK: {api_count} recording(s) for {target} on API, {local_count} local")
        return 0

    if api_count > local_count:
        missing = api_count - local_count
        print(f"WARNING: Plaud has {api_count} recording(s) for {target}, only {local_count} pulled locally. "
              f"{missing} likely missing (slow upload/processing or sync-state lockout).")
        print("To recover: lower _db/plaud-sync.json:last_sync_epoch_ms below the missing recording's start_time, "
              "then run pull-plaud.py.")
        for f in sorted(api_today, key=lambda x: x["start"]):
            ts = datetime.fromtimestamp(f["start"], tz=timezone.utc).strftime("%H:%M")
            print(f"  - {ts} UTC | {f['subject'][:80]}")
        return 0

    if not args.quiet:
        print(f"Plaud completeness: local count ({local_count}) exceeds API ({api_count}) for {target}, likely stale files in _attachments")
    return 0


if __name__ == "__main__":
    sys.exit(main())
