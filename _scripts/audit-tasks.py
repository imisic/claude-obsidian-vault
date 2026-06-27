#!/usr/bin/env python3
"""
audit-tasks.py: Audit and fix action items in interaction notes.

Delegates per-line transformation to apply_task_hygiene() in utils.py so the
deterministic rules live in one place (shared with write-notes.py).

Transformations applied:
- Sam-owned or already-delegated: only stamp [created::] when missing
- Boss-chain/stakeholder VIP present: only stamp [created::]
- 1on1 / sent email / 2-5 attendees + non-Sam owner: add [delegated-by:: [[Sam-Rivera]]]
- >5 attendees / >5 To+CC + non-Sam owner: strip checkbox to plain bullet

Usage:
    python3 audit-tasks.py [--vault PATH] [--fix] [--backfill-created] [--forgettability]

    --fix              Apply changes (default: dry-run)
    --backfill-created Also stamp [created::] on tasks lacking it
    --forgettability   Apply forgettability filter to Sam-owned tasks (demotes those
                       without time horizon / deliverable / small-ask verb). Use for
                       one-shot backfill of existing tasks.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import apply_task_hygiene, parse_task_line, stamp_created, load_vip_slugs, OWNER_SLUG  # noqa: E402


def parse_frontmatter(content: str) -> dict:
    """Quick frontmatter extraction."""
    fm = {}
    if not content.startswith("---"):
        return fm
    lines = content.split("\n")
    in_list = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("  - "):
            if in_list:
                fm.setdefault(in_list, []).append(line.strip().lstrip("- ").strip('"').strip("'"))
            continue
        in_list = None
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val:
                fm[key] = val
            else:
                in_list = key
    return fm


def _classify_change(before: str, after: str) -> str:
    """Label the kind of transformation for the report."""
    before_p = parse_task_line(before)
    after_p = parse_task_line(after)
    if before_p["is_task"] and not after_p["is_task"]:
        return "STRIP_CHECKBOX"
    if not before_p["delegated_by"] and after_p["delegated_by"]:
        return "ADD_DELEGATED_BY"
    if not before_p["has_created"] and after_p["has_created"]:
        return "STAMP_CREATED"
    return "OTHER"


def audit_file(filepath: Path, vault: Path, fix: bool, backfill_created: bool,
               forgettability: bool = False,
               vip_slugs: set | None = None) -> tuple[list[dict], bool]:
    """Apply hygiene to all task lines in a file.

    Returns (findings, modified). Findings list summarises transformations.

    forgettability=False (default): existing behavior, Sam-owned tasks only get
        [created::] stamped; forgettability demotion is bypassed.
    forgettability=True: full apply_task_hygiene() including the forgettability
        filter that demotes Sam-owned tasks lacking signals.
    """
    try:
        original = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return [], False

    fm = parse_frontmatter(original)
    rel_path = str(filepath.relative_to(vault))
    findings = []

    lines = original.split("\n")
    new_lines = []
    for i, line in enumerate(lines):
        parsed = parse_task_line(line)
        if not parsed["is_task"]:
            new_lines.append(line)
            continue

        # Default mode: preserve existing audit-tasks behavior, Sam-owned tasks
        # only get [created::] stamped. --forgettability opts into the
        # forgettability demotion path.
        if forgettability:
            transformed = apply_task_hygiene(line, fm, vip_slugs=vip_slugs)
        else:
            if (parsed["owner"] == OWNER_SLUG
                    and not parsed["delegated_by"]
                    and "[demoted::" not in line):
                # Bypass forgettability: just stamp [created::], same as old behavior.
                transformed = stamp_created(line, fm.get("date", ""))
            else:
                transformed = apply_task_hygiene(line, fm, vip_slugs=vip_slugs)

        if backfill_created and not parsed["has_created"]:
            transformed = stamp_created(transformed, fm.get("date", ""))

        if transformed != line:
            change_type = _classify_change(line, transformed)
            findings.append({
                "file": rel_path,
                "line": i + 1,
                "change": change_type,
                "before": line.strip()[:100],
                "after": transformed.strip()[:100],
            })
        new_lines.append(transformed)

    modified = any(new_lines[i] != lines[i] for i in range(len(lines)))
    if fix and modified:
        filepath.write_text("\n".join(new_lines), encoding="utf-8")
    return findings, modified


def main():
    parser = argparse.ArgumentParser(description="Audit action items for Sam-relevance")
    parser.add_argument("--vault", default=str(Path(__file__).resolve().parent.parent),
                        help="Vault root directory")
    parser.add_argument("--fix", action="store_true", help="Apply fixes (default: dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without applying (default)")
    parser.add_argument("--backfill-created", action="store_true",
                        help="Also stamp [created::] on tasks lacking it")
    parser.add_argument("--forgettability", action="store_true",
                        help="Apply forgettability filter to Sam-owned tasks (demotes those "
                             "without time horizon / deliverable / small-ask verb). "
                             "Use for one-shot backfill of existing tasks.")
    args = parser.parse_args()

    if args.fix and args.dry_run:
        print("Cannot use --fix and --dry-run together", file=sys.stderr)
        sys.exit(1)

    vault = Path(args.vault)
    interactions_dir = vault / "05-Interactions"
    vip_slugs = load_vip_slugs(vault)

    all_findings = []
    file_count = 0

    for md_file in sorted(interactions_dir.rglob("*.md")):
        file_count += 1
        findings, _ = audit_file(md_file, vault, fix=args.fix,
                                 backfill_created=args.backfill_created,
                                 forgettability=args.forgettability,
                                 vip_slugs=vip_slugs)
        all_findings.extend(findings)

    add_delegated = [f for f in all_findings if f["change"] == "ADD_DELEGATED_BY"]
    remove_checkbox = [f for f in all_findings if f["change"] == "STRIP_CHECKBOX"]
    stamp_created_changes = [f for f in all_findings if f["change"] == "STAMP_CREATED"]
    other = [f for f in all_findings if f["change"] == "OTHER"]

    mode = "FIXED" if args.fix else "DRY RUN"
    print(f"\n=== Task Audit ({mode}), scanned {file_count} files ===\n")

    if add_delegated:
        print(f"## Add [delegated-by:: [[Sam-Rivera]]] ({len(add_delegated)})")
        for f in add_delegated:
            verb = "FIXED" if args.fix else "WOULD FIX"
            print(f"  {verb}: {f['file']}:{f['line']}")
            print(f"    {f['before']}")
            print(f"    → {f['after']}")
        print()

    if remove_checkbox:
        print(f"## Strip checkbox → plain bullet ({len(remove_checkbox)})")
        for f in remove_checkbox:
            verb = "FIXED" if args.fix else "WOULD FIX"
            print(f"  {verb}: {f['file']}:{f['line']}")
            print(f"    {f['before']}")
            print(f"    → {f['after']}")
        print()

    if stamp_created_changes:
        print(f"## Stamp [created::] ({len(stamp_created_changes)})")
        if not args.fix:
            print(f"  (use --fix to apply)")
        by_file = {}
        for f in stamp_created_changes:
            by_file[f['file']] = by_file.get(f['file'], 0) + 1
        for fname, cnt in sorted(by_file.items())[:15]:
            print(f"  {fname}: {cnt} task(s)")
        if len(by_file) > 15:
            print(f"  ... and {len(by_file) - 15} more files")
        print()

    if other:
        print(f"## Other changes ({len(other)})")
        for f in other[:10]:
            print(f"  {f['file']}:{f['line']}: {f['before']} → {f['after']}")
        print()

    total = len(all_findings)
    print(f"Summary: {total} transformations across {file_count} files, "
          f"{len(add_delegated)} delegated, {len(remove_checkbox)} stripped, "
          f"{len(stamp_created_changes)} created-stamped, {len(other)} other")

    if not args.fix and total > 0:
        print(f"\nRun with --fix to apply changes")


if __name__ == "__main__":
    main()
