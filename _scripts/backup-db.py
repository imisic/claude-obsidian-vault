#!/usr/bin/env python3
"""
backup-db.py: Snapshot critical _db/ files before mutations.

Copies entity-registry.json, sanitize-mappings.json, and email-lookup.json
to _db/backups/YYYY-MM-DD/. Rotation is bounded three ways and always keeps the
most recent backup: by age (--keep-days), by count (--keep-count), and by total
size (--max-total-mb). _db/backups/ is already gitignored, so snapshots never
get committed.

Usage:
    python3 backup-db.py [--vault PATH] [--keep-days 7] [--keep-count 14] [--max-total-mb 200]
"""

import argparse
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path


CRITICAL_FILES = [
    "entity-registry.json",
    "sanitize-mappings.json",
    "email-lookup.json",
]


def dir_size(path):
    """Total size in bytes of all files under path."""
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def main():
    parser = argparse.ArgumentParser(description="Backup critical _db files")
    parser.add_argument("--vault", default=str(Path(__file__).resolve().parent.parent),
                        help="Vault root directory")
    parser.add_argument("--keep-days", type=int, default=7,
                        help="Max age of backups to retain, in days (default: 7)")
    parser.add_argument("--keep-count", type=int, default=14,
                        help="Max number of daily backups to retain (default: 14)")
    parser.add_argument("--max-total-mb", type=int, default=200,
                        help="Max total size of _db/backups/ in MB (default: 200)")
    args = parser.parse_args()

    vault = Path(args.vault)
    db_dir = vault / "_db"
    today = datetime.now().strftime("%Y-%m-%d")
    backup_dir = db_dir / "backups" / today

    # Skip if today's backup already exists
    if backup_dir.exists():
        existing = list(backup_dir.glob("*.json"))
        if len(existing) >= len(CRITICAL_FILES):
            print(f"Backup for {today} already exists ({len(existing)} files), skipping",
                  file=sys.stderr)
            sys.exit(0)

    backup_dir.mkdir(parents=True, exist_ok=True)
    copied = 0

    for filename in CRITICAL_FILES:
        src = db_dir / filename
        if src.exists():
            dst = backup_dir / filename
            shutil.copy2(str(src), str(dst))
            copied += 1
        else:
            print(f"Warning: {filename} not found, skipping", file=sys.stderr)

    # Rotate old backups. A day-dir is kept only if it is within the age window
    # AND within the count cap AND keeping it does not push the total over the
    # size cap. The most recent backup is always kept, even if it alone exceeds
    # a bound, so rotation can never wipe out every snapshot.
    backups_root = db_dir / "backups"
    cutoff = datetime.now() - timedelta(days=args.keep_days)
    max_bytes = args.max_total_mb * 1024 * 1024

    day_dirs = []
    for d in backups_root.iterdir():
        if not d.is_dir():
            continue
        try:
            d_date = datetime.strptime(d.name, "%Y-%m-%d")
        except ValueError:
            continue
        day_dirs.append((d_date, d))
    day_dirs.sort(reverse=True)  # newest first

    kept, to_remove, total = [], [], 0
    for i, (d_date, d) in enumerate(day_dirs):
        size = dir_size(d)
        if d_date >= cutoff and i < args.keep_count and total + size <= max_bytes:
            kept.append(d)
            total += size
        else:
            to_remove.append(d)

    # Never delete everything: always keep the most recent backup.
    if not kept and day_dirs:
        to_remove = [d for (_, d) in day_dirs[1:]]

    removed = 0
    for d in to_remove:
        shutil.rmtree(str(d))
        removed += 1

    parts = [f"Backed up {copied} files to {today}"]
    if removed:
        parts.append(f"rotated {removed} old backups")
    print(", ".join(parts), file=sys.stderr)


if __name__ == "__main__":
    main()
