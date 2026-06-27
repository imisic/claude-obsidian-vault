#!/usr/bin/env python3
"""
process-capture.py: Route daily-note ## Capture section to its destinations.

Reads the ## Capture section of a daily note; for each non-empty, non-comment line:
- `- [ ] description`  → appended to 07-Areas/My-Tasks.md under ## Open as a real task
- `- description`      → appended to the daily note's own ## Notes section
Section is cleared after processing (HTML comments preserved).

Usage:
    python3 process-capture.py --vault PATH [--date YYYY-MM-DD]

Output (stdout): JSON {processed_lines, tasks_added, notes_added, errors[]}
"""
import argparse
import json
import re
import sys
from datetime import date as date_cls
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import ensure_utf8_stdio, atomic_text_write, OWNER_SLUG

_CAPTURE_HEADER_RE = re.compile(r'^##\s+Capture\s*$', re.MULTILINE)
_NEXT_SECTION_RE = re.compile(r'^---\s*$|^##\s+', re.MULTILINE)
_NOTES_HEADER_RE = re.compile(r'^##\s+Notes\s*$', re.MULTILINE)
_OWNER_RE = re.compile(r'^\s*-\s*\[\s*\]\s*\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]')
_TASK_LINE_RE = re.compile(r'^\s*-\s*\[\s*\]\s*(.*)$')
_PLAIN_BULLET_RE = re.compile(r'^\s*-\s+(?!\[)(.*)$')


def find_section(content: str, header_pattern: re.Pattern) -> tuple | None:
    """Find (start, end) char offsets of a section's content (not including header)."""
    m = header_pattern.search(content)
    if not m:
        return None
    start = m.end()
    next_match = _NEXT_SECTION_RE.search(content, pos=start + 1)
    end = next_match.start() if next_match else len(content)
    return start, end


def parse_capture_lines(capture_text: str) -> tuple:
    """Split capture content into (task_lines, note_lines).

    Skips HTML comments and blank lines.
    """
    tasks = []
    notes = []
    in_comment = False
    for raw in capture_text.split("\n"):
        line = raw.rstrip()
        if not line:
            continue
        if "<!--" in line:
            in_comment = True
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if _TASK_LINE_RE.match(line):
            tasks.append(line)
        elif _PLAIN_BULLET_RE.match(line):
            notes.append(line)
        else:
            notes.append(f"- {line.lstrip('- ').strip()}")
    return tasks, notes


def format_task_for_my_tasks(line: str, target_date: str, daily_stem: str) -> str:
    """Turn a `- [ ] description` line into a fully-tagged My-Tasks entry.

    If no [[Owner]] wikilink present, defaults to [[Sam-Rivera]].
    Stamps [created::] and [source:: [[daily_stem]]].
    """
    m = _TASK_LINE_RE.match(line)
    if not m:
        return line
    rest = m.group(1).strip()
    om = _OWNER_RE.match(line)
    if not om:
        rest = f"[[{OWNER_SLUG}]] {rest}"
    parts = [f"- [ ] {rest}"]
    if "[created::" not in rest:
        parts.append(f"[created:: {target_date}]")
    if "[source::" not in rest:
        parts.append(f"[source:: [[{daily_stem}]]]")
    return " ".join(parts)


def append_to_my_tasks(vault: Path, task_lines: list) -> None:
    """Append task lines to 07-Areas/My-Tasks.md ## Open section.

    Creates the file if missing.
    """
    if not task_lines:
        return
    my_tasks = vault / "07-Areas" / "My-Tasks.md"
    if not my_tasks.exists():
        my_tasks.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "---\n"
            f"date: {date_cls.today().isoformat()}\n"
            "type: personal-tasks\n"
            "---\n\n"
            "# My Tasks\n\n"
            "Personal task bucket. Items dropped into a daily note's `## Capture` section land here.\n"
            "Closed items archive via `/w-task-audit`.\n\n"
            "## Open\n\n"
            + "\n".join(task_lines) + "\n\n"
            "## Done\n\n"
        )
        atomic_text_write(my_tasks, content)
        return
    existing = my_tasks.read_text(encoding="utf-8")
    open_header = re.search(r'^##\s+Open\s*$', existing, re.MULTILINE)
    if not open_header:
        new = existing.rstrip() + "\n\n## Open\n\n" + "\n".join(task_lines) + "\n"
    else:
        insert_at = open_header.end()
        new = existing[:insert_at] + "\n\n" + "\n".join(task_lines) + existing[insert_at:]
    atomic_text_write(my_tasks, new)


def append_to_notes(daily_path: Path, content: str, note_lines: list) -> str:
    """Append note lines to the daily note's ## Notes section."""
    if not note_lines:
        return content
    notes_match = _NOTES_HEADER_RE.search(content)
    if not notes_match:
        return content.rstrip() + "\n\n## Notes\n\n" + "\n".join(note_lines) + "\n"
    insert_at = notes_match.end()
    return content[:insert_at] + "\n\n" + "\n".join(note_lines) + content[insert_at:]


def clear_capture_section(content: str) -> str:
    """Replace Capture section content with just the comment template."""
    cap_range = find_section(content, _CAPTURE_HEADER_RE)
    if not cap_range:
        return content
    start, end = cap_range
    placeholder = (
        "\n<!--\n"
        "Drop tasks or quick thoughts here. /w-daily processes this section:\n"
        "  - [ ] thing  → routed to 07-Areas/My-Tasks.md as a tracked task\n"
        "  - thing      → routed to the Notes section below as an untracked note\n"
        "Section is cleared after processing. Comments (HTML) are preserved.\n"
        "-->\n\n"
    )
    return content[:start] + placeholder + content[end:]


def process(vault: Path, daily_path: Path, target_date: str = None) -> dict:
    """Main processing, returns summary dict."""
    target_date = target_date or date_cls.today().isoformat()
    daily_stem = daily_path.stem

    content = daily_path.read_text(encoding="utf-8")
    cap_range = find_section(content, _CAPTURE_HEADER_RE)
    if not cap_range:
        return {"processed_lines": 0, "tasks_added": 0, "notes_added": 0, "errors": ["no Capture section"]}
    start, end = cap_range
    capture_text = content[start:end]
    tasks, notes = parse_capture_lines(capture_text)
    if not tasks and not notes:
        return {"processed_lines": 0, "tasks_added": 0, "notes_added": 0, "errors": []}

    formatted_tasks = [format_task_for_my_tasks(t, target_date, daily_stem) for t in tasks]
    append_to_my_tasks(vault, formatted_tasks)

    if notes:
        content = append_to_notes(daily_path, content, notes)
    content = clear_capture_section(content)
    atomic_text_write(daily_path, content)
    return {
        "processed_lines": len(tasks) + len(notes),
        "tasks_added": len(tasks),
        "notes_added": len(notes),
        "errors": [],
    }


def main():
    ensure_utf8_stdio()
    p = argparse.ArgumentParser()
    p.add_argument("--vault", required=True)
    p.add_argument("--date", default=None, help="Daily note date (YYYY-MM-DD); defaults to today")
    args = p.parse_args()
    vault = Path(args.vault)
    target = args.date or date_cls.today().isoformat()
    daily = vault / "01-Daily" / target[:4] / f"{target}.md"
    if not daily.exists():
        print(json.dumps({"processed_lines": 0, "errors": [f"daily note not found: {daily}"]}))
        return
    result = process(vault, daily, target_date=target)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
