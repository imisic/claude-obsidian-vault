#!/usr/bin/env python3
"""
build-thread-index.py: Build a thread lookup index from existing interaction notes.

Scans 05-Interactions/**/*.md frontmatter for conversation-id and subject fields.
Outputs _db/thread-index.json for fast thread matching during ingestion.

Usage:
    python3 build-thread-index.py [--vault PATH]
    python3 build-thread-index.py [--vault PATH] --incremental
    python3 build-thread-index.py [--vault PATH] --rebuild
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
        elif line.startswith("type:"):
            val = line.split(":", 1)[1].strip().strip('"').strip("'")
            if val:
                result["type"] = val

    return result if result else None


def scan_files(vault: Path, interactions_dir: Path, md_files):
    """Scan a list of .md files and return index dicts and counters."""
    by_conversation_id: dict[str, list[dict]] = {}
    by_normalized_subject: dict[str, list[dict]] = {}
    total_scanned = 0
    total_indexed = 0

    for md_file in md_files:
        total_scanned += 1
        fm = extract_frontmatter(md_file)
        if not fm:
            continue

        # Only index email and meeting notes
        if fm.get("type") not in ("email", "meeting"):
            continue

        rel_path = str(md_file.relative_to(vault))
        entry = {
            "path": rel_path,
            "date": fm.get("date", ""),
            "subject": fm.get("subject", ""),
            "relevance": fm.get("relevance", ""),
        }

        conv_id = fm.get("conversation_id")
        if conv_id:
            by_conversation_id.setdefault(conv_id, []).append(entry)
            total_indexed += 1

        subject = fm.get("subject")
        if subject:
            norm = normalize_subject(subject)
            if norm:
                by_normalized_subject.setdefault(norm, []).append(entry)
                if not conv_id:
                    total_indexed += 1  # Only count once

    return by_conversation_id, by_normalized_subject, total_scanned, total_indexed


def validate_and_clean(vault: Path, by_conversation_id: dict, by_normalized_subject: dict):
    """Remove entries whose paths no longer exist on disk. Returns cleaned copies."""
    all_paths = set()
    for entries in by_conversation_id.values():
        all_paths.update(e["path"] for e in entries)
    for entries in by_normalized_subject.values():
        all_paths.update(e["path"] for e in entries)
    valid_paths = {p for p in all_paths if (vault / p).exists()}

    for entries in by_conversation_id.values():
        entries[:] = [e for e in entries if e["path"] in valid_paths]
    for entries in by_normalized_subject.values():
        entries[:] = [e for e in entries if e["path"] in valid_paths]

    # Remove empty keys
    by_conversation_id = {k: v for k, v in by_conversation_id.items() if v}
    by_normalized_subject = {k: v for k, v in by_normalized_subject.items() if v}

    return by_conversation_id, by_normalized_subject


def merge_into(existing: dict[str, list[dict]], new: dict[str, list[dict]]):
    """Merge new index entries into existing, deduplicating by path within each key."""
    for key, new_entries in new.items():
        if key not in existing:
            existing[key] = new_entries
        else:
            existing_paths = {e["path"] for e in existing[key]}
            for entry in new_entries:
                if entry["path"] not in existing_paths:
                    existing[key].append(entry)
                    existing_paths.add(entry["path"])


def main():
    parser = argparse.ArgumentParser(description="Build thread index from interaction notes")
    parser.add_argument("--vault", default=str(Path(__file__).resolve().parent.parent),
                        help="Vault root directory")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--incremental", action="store_true",
                      help="Only scan files newer than the existing index")
    mode.add_argument("--rebuild", action="store_true",
                      help="Full rebuild (default behavior, explicit alias)")
    args = parser.parse_args()

    vault = Path(args.vault)
    interactions_dir = vault / "05-Interactions"
    output_path = vault / "_db" / "thread-index.json"

    # Incremental mode
    if args.incremental and output_path.exists():
        index_mtime = output_path.stat().st_mtime

        # Find .md files newer than the index
        new_files = []
        if interactions_dir.exists():
            for md_file in interactions_dir.rglob("*.md"):
                if md_file.stat().st_mtime > index_mtime:
                    new_files.append(md_file)

        if not new_files:
            print("Thread index up-to-date (0 new files)", file=sys.stderr)
            return

        # Load existing index
        with open(output_path, "r", encoding="utf-8") as f:
            existing_index = json.load(f)

        by_conv = existing_index.get("by_conversation_id", {})
        by_subj = existing_index.get("by_normalized_subject", {})

        # Scan only the new files
        new_conv, new_subj, scanned, indexed = scan_files(vault, interactions_dir, new_files)

        # Merge new entries into existing index
        merge_into(by_conv, new_conv)
        merge_into(by_subj, new_subj)

        # Validate all paths (catches deletions/renames since last full rebuild)
        by_conv, by_subj = validate_and_clean(vault, by_conv, by_subj)

        index = {
            "by_conversation_id": by_conv,
            "by_normalized_subject": by_subj,
        }

        atomic_json_write(output_path, index)

        print(f"Incremental: scanned {scanned} new files, added {indexed} entries "
              f"(total: {len(by_conv)} conv-ids, {len(by_subj)} subjects)",
              file=sys.stderr)
        return

    # Full rebuild (default, or --rebuild, or --incremental with no existing index)
    if args.incremental and not output_path.exists():
        print("No existing index found, doing full rebuild", file=sys.stderr)

    all_files = list(interactions_dir.rglob("*.md")) if interactions_dir.exists() else []
    by_conversation_id, by_normalized_subject, total_scanned, total_indexed = \
        scan_files(vault, interactions_dir, all_files)

    by_conversation_id, by_normalized_subject = \
        validate_and_clean(vault, by_conversation_id, by_normalized_subject)

    index = {
        "by_conversation_id": by_conversation_id,
        "by_normalized_subject": by_normalized_subject,
    }

    # Write index
    atomic_json_write(output_path, index)

    print(f"Scanned {total_scanned} files, indexed {total_indexed} entries "
          f"({len(by_conversation_id)} conv-ids, {len(by_normalized_subject)} subjects)",
          file=sys.stderr)


if __name__ == "__main__":
    main()
