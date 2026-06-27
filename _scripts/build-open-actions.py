#!/usr/bin/env python3
"""
build-open-actions.py: Extract action items from vault notes.

Scans 05-Interactions/**/*.md, 03-Projects/**/*.md, and
07-Areas/06-Organization/**/*.md (Partners, Products hub pages) for:
- Open (unchecked) `- [ ]` lines → indexed by owner and mentioned people
- Completed (checked) `- [x]` lines → flat list sorted by note_date descending

Usage:
    python3 build-open-actions.py [--vault PATH]

Output: _db/open-actions.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import atomic_json_write


# Match: - [ ] [[Owner]] description [due:: date] [source:: [[note]]]
# Also match custom open statuses: [/] In Progress, [>] Delegated, [!] Urgent
ACTION_RE = re.compile(r'^[-*]\s*\[([ />!])\]\s*(.*)')
COMPLETED_RE = re.compile(r'^[-*]\s*\[x\]\s*(.*)', re.IGNORECASE)
CANCELLED_RE = re.compile(r'^[-*]\s*\[-\]\s*(.*)')
STATUS_SYMBOLS = {' ': 'todo', '/': 'in-progress', '>': 'delegated', '!': 'urgent'}
OWNER_RE = re.compile(r'^\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]\s*(.*)')
DUE_RE = re.compile(r'\[due::\s*(\d{4}-\d{2}-\d{2})\]')
SOURCE_RE = re.compile(r'\[source::\s*\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]\]')
DELEGATED_RE = re.compile(r'\[delegated-by::\s*\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]\]')
CREATED_RE = re.compile(r'\[created::\s*(\d{4}-\d{2}-\d{2})\]')
DEMOTED_RE = re.compile(r'\[demoted::\s*(\w+)\]')
WIKILINK_RE = re.compile(r'\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]')


def extract_date_from_frontmatter(filepath: Path) -> str:
    """Quick extraction of date from frontmatter."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(1000)
    except Exception:
        return ""

    if not content.startswith("---"):
        return ""

    for line in content.split("\n")[1:20]:
        line = line.strip()
        if line == "---":
            break
        if line.startswith("date:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return ""


def _parse_action_line(rest: str, rel_path: str, note_stem: str, note_date: str) -> dict | None:
    """Parse an action item line (after the checkbox) into a structured dict."""
    rest = rest.strip()
    if not rest:
        return None

    # Parse owner
    owner = ""
    description = rest
    om = OWNER_RE.match(rest)
    if om:
        owner = om.group(1)
        description = om.group(2).strip()

    # Parse metadata
    due = ""
    dm = DUE_RE.search(description)
    if dm:
        due = dm.group(1)
        description = DUE_RE.sub("", description).strip()

    source = note_stem
    sm = SOURCE_RE.search(description)
    if sm:
        source = sm.group(1)
        description = SOURCE_RE.sub("", description).strip()

    delegated_by = ""
    dbm = DELEGATED_RE.search(description)
    if dbm:
        delegated_by = dbm.group(1)
        description = DELEGATED_RE.sub("", description).strip()

    created = ""
    cm = CREATED_RE.search(description)
    if cm:
        created = cm.group(1)
        description = CREATED_RE.sub("", description).strip()

    # Find all mentioned people in the description
    mentioned = [w for w in WIKILINK_RE.findall(description) if w != owner]

    action = {
        "owner": owner,
        "description": description,
        "due": due,
        "source": source,
        "source_path": rel_path,
        "note_date": note_date,
        "created": created or note_date,
        "mentioned": mentioned,
    }
    if delegated_by:
        action["delegated_by"] = delegated_by

    return action


def scan_file(filepath: Path, vault: Path) -> tuple[list[dict], list[dict]]:
    """Backwards-compatible 2-tuple: (open_actions, completed_actions)."""
    open_actions, completed_actions, _ = scan_file_with_demoted(filepath, vault)
    return open_actions, completed_actions


def scan_file_with_demoted(filepath: Path, vault: Path) -> tuple[list[dict], list[dict], list[dict]]:
    """Scan a file for action items. Returns (open_actions, completed_actions, demoted_actions).

    Demoted lines (containing `[demoted:: <reason>]`) are excluded from the open
    bucket regardless of whether their checkbox remains intact. This is the
    safety net for any agent output that slips past apply_task_hygiene.
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return [], [], []

    open_actions = []
    completed_actions = []
    demoted_actions = []
    rel_path = str(filepath.relative_to(vault))
    note_stem = filepath.stem
    note_date = extract_date_from_frontmatter(filepath)

    for line in lines:
        stripped = line.lstrip().rstrip()

        # Demoted lines: skip from open/completed; record separately.
        # Match BEFORE ACTION_RE/COMPLETED_RE so a defensive `- [ ] ... [demoted::]`
        # also gets routed here rather than counted as open.
        dm = DEMOTED_RE.search(stripped)
        if dm and (stripped.startswith("-") or stripped.startswith("*")):
            tail = stripped
            am = ACTION_RE.match(stripped) or COMPLETED_RE.match(stripped) or CANCELLED_RE.match(stripped)
            if am:
                # ACTION_RE/COMPLETED_RE/CANCELLED_RE captures the trailing description
                # in different groups; pick whichever is non-empty.
                tail = am.group(am.lastindex)
            else:
                # Plain bullet, strip leading `- ` / `* `
                tail = stripped.lstrip("-*").lstrip()
            action = _parse_action_line(tail, rel_path, note_stem, note_date)
            if action:
                action["description"] = DEMOTED_RE.sub("", action["description"]).strip()
                action["demoted_reason"] = dm.group(1)
                demoted_actions.append(action)
            continue

        # Check open actions (including custom statuses: /, >, !)
        m = ACTION_RE.match(stripped)
        if m:
            status_symbol = m.group(1)
            action = _parse_action_line(m.group(2), rel_path, note_stem, note_date)
            if action:
                action["status"] = STATUS_SYMBOLS.get(status_symbol, "todo")
                open_actions.append(action)
            continue

        # Check completed actions
        m = COMPLETED_RE.match(stripped)
        if m:
            action = _parse_action_line(m.group(1), rel_path, note_stem, note_date)
            if action:
                completed_actions.append(action)
            continue

        # Check cancelled actions (treat as completed, not open)
        m = CANCELLED_RE.match(stripped)
        if m:
            action = _parse_action_line(m.group(1), rel_path, note_stem, note_date)
            if action:
                action["status"] = "cancelled"
                completed_actions.append(action)

    return open_actions, completed_actions, demoted_actions


def main():
    parser = argparse.ArgumentParser(description="Build open actions index")
    parser.add_argument("--vault", default=str(Path(__file__).resolve().parent.parent),
                        help="Vault root directory")
    parser.add_argument("--skip-if-recent", type=int, default=0, metavar="SECONDS",
                        help="Skip rebuild if output file is younger than SECONDS")
    args = parser.parse_args()

    vault = Path(args.vault)
    output_path = vault / "_db" / "open-actions.json"

    if args.skip_if_recent > 0 and output_path.exists():
        import time
        age = time.time() - output_path.stat().st_mtime
        if age < args.skip_if_recent:
            print(f"open-actions.json is {int(age)}s old (< {args.skip_if_recent}s), skipping",
                  file=sys.stderr)
            sys.exit(0)

    all_open = []
    all_completed = []
    all_demoted = []
    scan_dirs = [
        vault / "05-Interactions",
        vault / "03-Projects",
        # Organization hub pages (Partners, Products) accumulate real Sam-relevant
        # tasks tied to a partner/product. They are a legitimate task surface, so
        # index them too, otherwise checkboxes here are a silent second task truth.
        # My-Tasks.md lives at 07-Areas root (not under 06-Organization), so it is
        # not double-scanned by this entry.
        vault / "07-Areas" / "06-Organization",
    ]

    total_files = 0
    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        for md_file in scan_dir.rglob("*.md"):
            total_files += 1
            open_actions, completed_actions, demoted = scan_file_with_demoted(md_file, vault)
            all_open.extend(open_actions)
            all_completed.extend(completed_actions)
            all_demoted.extend(demoted)

    extra_files = [
        vault / "07-Areas" / "My-Tasks.md",
    ]

    for extra in extra_files:
        if not extra.exists():
            continue
        total_files += 1
        open_actions, completed_actions, demoted = scan_file_with_demoted(extra, vault)
        all_open.extend(open_actions)
        all_completed.extend(completed_actions)
        all_demoted.extend(demoted)

    # Sort by date descending
    all_open.sort(key=lambda a: a.get("note_date", ""), reverse=True)
    all_completed.sort(key=lambda a: a.get("note_date", ""), reverse=True)
    all_demoted.sort(key=lambda a: a.get("note_date", ""), reverse=True)

    # Build indexes (open actions only, preserves 1on1 prep compatibility)
    by_owner: dict[str, list[dict]] = {}
    by_person: dict[str, list[dict]] = {}  # includes owner + mentioned

    for action in all_open:
        owner = action.get("owner", "")
        if owner:
            by_owner.setdefault(owner, []).append(action)
            by_person.setdefault(owner, []).append(action)

        for person in action.get("mentioned", []):
            by_person.setdefault(person, []).append(action)

        delegated_by = action.get("delegated_by", "")
        if delegated_by and delegated_by not in (owner, ""):
            by_person.setdefault(delegated_by, []).append(action)

    output = {
        "total_open": len(all_open),
        "total_completed": len(all_completed),
        "total_demoted": len(all_demoted),
        "by_owner": by_owner,
        "by_person": by_person,
        "completed_actions": all_completed,
        "demoted_actions": all_demoted,
    }

    atomic_json_write(output_path, output)

    print(f"Scanned {total_files} files, found {len(all_open)} open + "
          f"{len(all_completed)} completed + {len(all_demoted)} demoted actions, "
          f"{len(by_owner)} owners, {len(by_person)} people referenced", file=sys.stderr)


if __name__ == "__main__":
    main()
