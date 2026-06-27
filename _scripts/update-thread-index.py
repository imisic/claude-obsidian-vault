#!/usr/bin/env python3
"""
update-thread-index.py: Append new note entries to the thread index.

After notes are created by write-notes.py, this script updates
_db/thread-index.json with the new entries so future runs find them
without a full rebuild.

Usage:
    python _scripts/update-thread-index.py --vault "." --notes "path1.md" "path2.md" ...
    echo -e "path1.md\\npath2.md" | python _scripts/update-thread-index.py --vault "." --stdin
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add _scripts/ to path for shared utils
sys.path.insert(0, str(Path(__file__).parent))
from utils import normalize_subject, atomic_json_write


def extract_frontmatter(filepath: Path) -> dict | None:
    """Extract YAML frontmatter fields we care about."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(4000)  # Frontmatter is always near the top
    except Exception:
        return None

    if not content.startswith("---"):
        return None

    fm_end = content.find("---", 3)
    if fm_end < 0:
        return None

    fm_block = content[3:fm_end]
    result = {}

    for line in fm_block.split("\n"):
        line = line.strip()
        if line.startswith("conversation-id:"):
            val = line.split(":", 1)[1].strip().strip('"').strip("'")
            if val:
                result["conversation_id"] = val
        elif line.startswith("subject:"):
            val = line.split(":", 1)[1].strip().strip('"').strip("'")
            if val:
                result["subject"] = val
        elif line.startswith("date:"):
            val = line.split(":", 1)[1].strip().strip('"').strip("'")
            if val:
                result["date"] = val
        elif line.startswith("relevance:"):
            val = line.split(":", 1)[1].strip().strip('"').strip("'")
            if val:
                result["relevance"] = val

    return result if result else None


def main():
    parser = argparse.ArgumentParser(
        description="Append new note entries to _db/thread-index.json after note creation"
    )
    parser.add_argument(
        "--vault", default=".",
        help="Vault root directory (default: current directory)"
    )
    parser.add_argument(
        "--notes", nargs="*", default=[],
        help="Paths to note files (relative to vault root)"
    )
    parser.add_argument(
        "--stdin", action="store_true",
        help="Read note paths from stdin (one per line)"
    )
    args = parser.parse_args()

    vault = Path(args.vault).resolve()
    index_path = vault / "_db" / "thread-index.json"

    if not index_path.exists():
        print(f"Error: thread index not found at {index_path}", file=sys.stderr)
        sys.exit(1)

    # Collect note paths
    note_paths = list(args.notes) if args.notes else []
    if args.stdin:
        for line in sys.stdin:
            line = line.strip()
            if line:
                note_paths.append(line)

    if not note_paths:
        print("No note paths provided. Use --notes or --stdin.", file=sys.stderr)
        sys.exit(1)

    # Load existing index
    with open(index_path, "r", encoding="utf-8") as f:
        index = json.load(f)

    by_conversation_id = index.get("by_conversation_id", {})
    by_normalized_subject = index.get("by_normalized_subject", {})

    added = 0
    skipped = 0

    for note_path_str in note_paths:
        # Resolve the full path for reading, keep relative for storage
        note_path = Path(note_path_str)
        if note_path.is_absolute():
            full_path = note_path
            rel_path = str(note_path.relative_to(vault))
        else:
            full_path = vault / note_path
            rel_path = note_path_str

        if not full_path.exists():
            print(f"Warning: file not found, skipping: {full_path}", file=sys.stderr)
            skipped += 1
            continue

        fm = extract_frontmatter(full_path)
        if not fm:
            skipped += 1
            continue

        # Skip notes without conversation-id AND without subject (non-email notes)
        conv_id = fm.get("conversation_id")
        subject = fm.get("subject")
        if not conv_id and not subject:
            skipped += 1
            continue

        entry = {
            "path": rel_path.replace("\\", "/"),
            "date": fm.get("date", ""),
            "subject": subject or "",
            "relevance": fm.get("relevance", ""),
        }

        entry_added = False

        # Add to by_conversation_id
        if conv_id:
            existing_entries = by_conversation_id.setdefault(conv_id, [])
            existing_paths = {e["path"] for e in existing_entries}
            if entry["path"] not in existing_paths:
                existing_entries.append(entry)
                entry_added = True

        # Add to by_normalized_subject
        if subject:
            norm = normalize_subject(subject)
            if norm:
                existing_entries = by_normalized_subject.setdefault(norm, [])
                existing_paths = {e["path"] for e in existing_entries}
                if entry["path"] not in existing_paths:
                    existing_entries.append(entry)
                    entry_added = True

        if entry_added:
            added += 1
        else:
            skipped += 1

    # Write updated index
    updated_index = {
        "by_conversation_id": by_conversation_id,
        "by_normalized_subject": by_normalized_subject,
    }

    atomic_json_write(Path(index_path), updated_index)

    print(f"Thread index updated: {added} entries added, {skipped} skipped (already present)")


if __name__ == "__main__":
    main()
