#!/usr/bin/env python3
"""
rebuild-daily-from-notes.py: Rebuild daily briefing sections from final notes.

This is a migration/backfill helper for older daily notes whose generated
sections were built from pre-hygiene briefing_data. It scans final
05-Interactions/YYYY/*.md notes, extracts summaries/decisions, and lets
build-daily-briefings.py render actions from the final note bodies.

Only the generated block between the H1 and `## Today's focus` is replaced.
Manual focus/notes content is preserved.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import atomic_text_write  # noqa: E402


SCRIPT_DIR = Path(__file__).resolve().parent


def _load_daily_builder():
    spec = importlib.util.spec_from_file_location(
        "build_daily_briefings",
        SCRIPT_DIR / "build-daily-briefings.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    if end == -1:
        return "", text
    return text[4:end], text[end + 4 :]


def parse_frontmatter(fm_text: str) -> dict:
    fm: dict[str, str | list[str]] = {}
    current_key = None
    for raw in fm_text.splitlines():
        if not raw.strip():
            continue
        if raw.startswith("  - ") and current_key:
            fm.setdefault(current_key, [])
            if isinstance(fm[current_key], list):
                fm[current_key].append(_clean_scalar(raw.strip()[2:]))
            continue
        current_key = None
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value:
            fm[key] = _clean_scalar(value)
        else:
            fm[key] = []
            current_key = key
    return fm


def _clean_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def first_heading(body: str) -> str:
    m = re.search(r"^#\s+(.+?)\s*$", body, re.MULTILINE)
    return m.group(1).strip() if m else ""


def section_bullets(body: str, title: str) -> list[str]:
    pattern = rf"^##\s+{re.escape(title)}\s*$\n(.*?)(?=^##\s+|\Z)"
    m = re.search(pattern, body, re.MULTILINE | re.DOTALL)
    if not m:
        return []
    bullets = []
    for line in m.group(1).splitlines():
        item = re.match(r"^\s*-\s+(.*\S)\s*$", line)
        if item:
            bullets.append(item.group(1).strip())
    return bullets


def entry_from_note(path: Path, vault: Path) -> dict | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    fm_text, body = split_frontmatter(text)
    fm = parse_frontmatter(fm_text)
    date = fm.get("date")
    if not isinstance(date, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return None
    note_type = fm.get("interaction-type") or fm.get("type")
    if note_type not in {"meeting", "email", "async", "reference"}:
        return None
    rel = str(path.relative_to(vault))
    summary = fm.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        summary = first_heading(body)
    subject = fm.get("subject")
    if not isinstance(subject, str) or not subject.strip():
        subject = first_heading(body) or summary
    vip = fm.get("vip-involved", [])
    if not isinstance(vip, list):
        vip = [str(vip)] if vip else []
    entry = {
        "date": date,
        "type": note_type,
        "subject": subject,
        "summary": summary,
        "note_path": rel,
        "output_file": rel,
        "vip_involved": vip,
        "decisions": section_bullets(body, "Decisions"),
        "relevance": fm.get("relevance", ""),
    }
    if isinstance(fm.get("recording-quality"), str):
        entry["recording_quality"] = fm["recording-quality"]
    return entry


def collect_entries(vault: Path, year: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    interactions_dir = vault / "05-Interactions" / year
    for path in sorted(interactions_dir.glob("*.md")):
        entry = entry_from_note(path, vault)
        if not entry:
            continue
        grouped.setdefault(entry["date"], []).append(entry)
    references_dir = vault / "08-Reference"
    for path in sorted(references_dir.glob("*.md")):
        if not path.name.startswith(year):
            continue
        entry = entry_from_note(path, vault)
        if not entry:
            continue
        grouped.setdefault(entry["date"], []).append(entry)
    return grouped


def is_daily_note(path: Path) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}\.md$", path.name))


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebuild daily generated sections from final interaction notes")
    ap.add_argument("--vault", default=".")
    ap.add_argument("--year", default="2026")
    ap.add_argument("--date", action="append", help="Specific date to rebuild; can be repeated")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--create-missing", action="store_true")
    args = ap.parse_args()

    vault = Path(args.vault).resolve()
    daily_builder = _load_daily_builder()
    grouped = collect_entries(vault, args.year)
    wanted = set(args.date or grouped.keys())
    result = {"updated": [], "written": [], "skipped": [], "errors": []}

    for target_date in sorted(wanted):
        entries = grouped.get(target_date, [])
        if not entries:
            result["skipped"].append(f"{target_date}: no interaction entries")
            continue
        note_path = vault / "01-Daily" / target_date[:4] / f"{target_date}.md"
        try:
            briefing = daily_builder.build_briefing(entries, target_date=target_date, vault=vault)
            rel = str(note_path.relative_to(vault))
            if note_path.exists():
                if not is_daily_note(note_path):
                    result["skipped"].append(f"{rel}: not daily note filename")
                    continue
                existing = note_path.read_text(encoding="utf-8", errors="replace")
                new_content = daily_builder.merge_briefing_into_existing(existing, briefing)
                if new_content != existing:
                    if not args.dry_run:
                        atomic_text_write(note_path, new_content)
                    result["updated"].append(rel)
                else:
                    result["skipped"].append(f"{rel}: unchanged")
            elif args.create_missing:
                note_path.parent.mkdir(parents=True, exist_ok=True)
                if not args.dry_run:
                    atomic_text_write(note_path, daily_builder.build_new_daily_note(target_date, briefing))
                result["written"].append(rel)
            else:
                result["skipped"].append(f"{rel}: missing")
        except Exception as exc:
            result["errors"].append(f"{target_date}: {exc}")

    print(result)
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
