#!/usr/bin/env python3
"""
build-daily-briefings.py: Deterministic daily note builder.

Consumes aggregated briefing_data[] from email-processor and transcript-processor
outputs and writes or updates daily notes under 01-Daily/YYYY/.

This replaces the per-day hand-authored markdown that /w-daily used to do in the
main LLM context. Structure, ordering, wikilinks, and VIP markers are applied
deterministically. The only remaining LLM-friendly touches (sign-off, attention
section) are left as optional overrides passed in via --overrides.

Usage:
    python3 build-daily-briefings.py --vault PATH --inputs FILE [FILE ...]
                                     [--target-date YYYY-MM-DD]
                                     [--overrides FILE]

Inputs may be either:
  - structured outputs from write-notes.py's inputs (with "briefing_data" key)
  - direct briefing_data arrays
  - the agent pointer JSONs (if they embed briefing_data)

Overrides file (JSON):
{
  "2026-04-14": {
    "sign_off": "Custom snarky line.",
    "attention_needed": ["Bullet 1", "Bullet 2"]
  }
}

Output (stdout):
{
  "written": ["01-Daily/2026/2026-04-14.md", ...],
  "updated": ["01-Daily/2026/2026-04-13.md"],
  "skipped": [],
  "errors": []
}
"""

import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import ensure_utf8_stdio, atomic_text_write, OWNER_SLUG

# Reuse the open-actions indexer's parser so the daily note renders actions from
# the FINAL written note body (post-hygiene), not from the pre-hygiene
# briefing_data.actions copy. write-notes.py applies task hygiene/demotion to the
# note body only (briefing_data.actions keeps the unfiltered copy) so a task
# demoted to a plain bullet would otherwise still show as an action in the daily.
# scan_file_with_demoted() already excludes demoted + completed lines.
import importlib.util as _ilu
_boa_spec = _ilu.spec_from_file_location(
    "build_open_actions", Path(__file__).parent / "build-open-actions.py")
_boa = _ilu.module_from_spec(_boa_spec)
_boa_spec.loader.exec_module(_boa)
scan_file_with_demoted = _boa.scan_file_with_demoted


WEEK_NUMBER_FALLBACK_YEAR = 2026  # kept for isoweek calculation

ACTIONS_CAP = 5    # per group (Sam-owned / waiting-on-others); audit finding #1/#6
DECISIONS_CAP = 7  # explicit decisions only; audit finding #1
EMAILS_CAP = 5     # daily scan layer: keep email tables short
DOCS_CAP = 5       # reference/doc ingests should stay visible but compact


# ---------------------------------------------------------------------------
# Briefing data aggregation
# ---------------------------------------------------------------------------

def load_briefing_data(input_paths: list[str]) -> list[dict]:
    """Load and concatenate briefing_data[] from one or more JSON files.

    Accepts any of:
      - {"briefing_data": [...]} (e.g., email-out.json / transcript-out.json)
      - {"notes": [...], "briefing_data": [...]} (write-input shape)
      - Plain list of briefing entries

    Per-note briefing_data is authoritative: when a file has both per-note and
    top-level briefing_data, the top-level is ignored (it would otherwise be
    counted twice and double the daily-note sections).

    A final dedup pass keyed by note_path drops any remaining duplicates that
    slipped in via separate input files.
    """
    all_entries = []
    for path in input_paths:
        p = Path(path)
        if not p.exists():
            print(f"Warning: input file not found: {path}", file=sys.stderr)
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"Warning: could not parse {path}: {e}", file=sys.stderr)
            continue

        if isinstance(data, list):
            all_entries.extend(data)
            continue

        if not isinstance(data, dict):
            continue

        note_bds = []
        notes = data.get("notes")
        if isinstance(notes, list):
            for note in notes:
                if not isinstance(note, dict):
                    continue
                nbd = note.get("briefing_data")
                if not isinstance(nbd, dict):
                    continue
                if "output_file" in nbd and "note_path" not in nbd:
                    nbd["note_path"] = nbd["output_file"]
                note_bds.append(nbd)

        if note_bds:
            all_entries.extend(note_bds)
        else:
            bd = data.get("briefing_data")
            if isinstance(bd, list):
                for entry in bd:
                    if isinstance(entry, dict) and "output_file" in entry and "note_path" not in entry:
                        entry["note_path"] = entry["output_file"]
                all_entries.extend(bd)

    seen = set()
    deduped = []
    for e in all_entries:
        key = e.get("note_path") or e.get("output_file") or id(e)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)
    return deduped


def group_by_date(entries: list[dict]) -> dict:
    """Group briefing entries by date. Skips entries with missing date."""
    grouped = {}
    for e in entries:
        d = e.get("date", "")
        if not d or not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            continue
        grouped.setdefault(d, []).append(e)
    return grouped


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _note_stem(note_path: str) -> str:
    return Path(note_path).stem


def _vip_prefix(vip_involved: list[str]) -> str:
    if not vip_involved:
        return ""
    if "boss-chain" in vip_involved:
        return "**!**"
    if "stakeholder" in vip_involved:
        return "*"
    return ""


def render_meetings(meetings: list[dict]) -> list[str]:
    if not meetings:
        return []
    lines = [f"## Meetings today ({len(meetings)})", ""]
    for m in sorted(meetings, key=lambda x: x.get("note_path", "")):
        summary = (m.get("summary") or "").strip()
        stem = _note_stem(m.get("note_path", ""))
        quality = m.get("recording_quality")
        quality_note = f" _(recording {quality})_" if quality else ""
        # Lite-mode deferred transcripts: the meeting note is a thin stub, raw
        # transcript parked in _attachments/. Flag it so the briefing is honest
        # about what was (not) captured. Run /w-daily --upgrade-deferred later.
        deferred_note = " _(deferred, not yet synthesized)_" if m.get("deferred") else ""
        lines.append(f"- {summary} → [[{stem}|note]]{quality_note}{deferred_note}")
    lines.append("")
    return lines


def _escape_pipe(text: str) -> str:
    """Escape pipes in wikilinks inside table cells (Obsidian rule)."""
    return re.sub(r"\[\[([^\[\]]+?)\|([^\[\]]+?)\]\]", r"[[\1\\|\2]]", text)


def render_key_emails(emails: list[dict]) -> list[str]:
    if not emails:
        return []
    lines = ["## Key emails", "", "| | Topic | Summary | Source |", "|-|-------|---------|--------|"]
    relevance_rank = {"high": 0, "medium": 1, "low": 2}
    ordered = sorted(
        emails,
        key=lambda x: (relevance_rank.get(x.get("relevance", ""), 9), x.get("note_path", "")),
    )
    for e in ordered[:EMAILS_CAP]:
        marker = _vip_prefix(e.get("vip_involved", []))
        marker_col = marker if marker else ""
        subject = (e.get("subject") or "").replace("|", "/")
        summary = (e.get("summary") or "").replace("|", "/")
        # Strip any leading VIP markers agents may have injected into the summary
        summary = re.sub(r"^\*\*!\*\*\s*", "", summary)
        summary = re.sub(r"^\*\s+", "", summary)
        stem = _note_stem(e.get("note_path", ""))
        source_cell = _escape_pipe(f"[[{stem}|note]]")
        lines.append(f"| {marker_col} | {subject} | {summary} | {source_cell} |")
    overflow = len(ordered) - EMAILS_CAP
    if overflow > 0:
        lines.append(f"|  | …and {overflow} more | See source notes for additional emails. |  |")
    lines.append("")
    return lines


def render_reference_docs(docs: list[dict]) -> list[str]:
    if not docs:
        return []
    ordered = sorted(docs, key=lambda x: x.get("note_path", ""))
    lines = ["## Reference docs", ""]
    for d in ordered[:DOCS_CAP]:
        summary = (d.get("summary") or d.get("subject") or "").strip()
        stem = _note_stem(d.get("note_path", ""))
        lines.append(f"- {summary} → [[{stem}|note]]")
    overflow = len(ordered) - DOCS_CAP
    if overflow > 0:
        lines.append(f"- …and {overflow} more in reference notes")
    lines.append("")
    return lines


def render_decisions(all_entries: list[dict]) -> list[str]:
    decisions = []
    seen = set()
    for e in all_entries:
        for d in e.get("decisions", []) or []:
            key = d.strip()
            if key and key not in seen:
                seen.add(key)
                decisions.append(d)
    if not decisions:
        return []
    lines = ["## Decisions made", ""]
    for d in decisions[:DECISIONS_CAP]:
        lines.append(f"- {d}")
    overflow = len(decisions) - DECISIONS_CAP
    if overflow > 0:
        lines.append(f"- …and {overflow} more in source notes")
    lines.append("")
    return lines


def _open_actions_from_note(note_path: str, vault: Path | None):
    """Open, non-demoted actions from the FINAL written note (post-hygiene).

    Returns a list of action dicts, or None if the note can't be read (caller
    falls back to the pre-hygiene briefing_data copy so nothing is ever lost).
    """
    if not note_path or vault is None:
        return None
    p = Path(note_path)
    if not p.is_absolute():
        p = vault / note_path
    if not p.exists():
        return None
    open_actions, _completed, _demoted = scan_file_with_demoted(p, vault)
    return open_actions


def render_action_items(all_entries: list[dict], vault: Path | None = None) -> list[str]:
    """Emit plain-text action references (no checkboxes) per CLAUDE.md.

    Source of truth is the FINAL written note body (post task-hygiene), read via
    the same parser as the open-actions index, so demoted/hygiene-stripped tasks
    never leak into the daily. Falls back to the entry's pre-hygiene
    briefing_data.actions only when the note file is unreadable.

    Split into Sam-owned vs waiting-on-others and capped (audit findings #1/#6);
    overflow is surfaced, never silently dropped.
    """
    owner_lines: list[str] = []
    other_lines: list[str] = []

    for e in all_entries:
        marker = _vip_prefix(e.get("vip_involved", []))
        prefix = f"{marker} " if marker else ""
        note_path = e.get("note_path") or e.get("output_file") or ""
        stem = _note_stem(note_path)

        acts = _open_actions_from_note(note_path, vault)
        if acts is not None:
            for a in acts:
                desc = (a.get("description") or "").strip()
                if not desc:
                    continue
                owner = a.get("owner", "")
                src = a.get("source") or stem
                owner_part = f"[[{owner}]] " if owner else ""
                line = f"- {prefix}{owner_part}{desc} → [[{src}|Source]]"
                (owner_lines if owner == OWNER_SLUG else other_lines).append(line)
        else:
            # Fallback: note file unreadable, use the pre-hygiene copy.
            for a in e.get("actions", []) or []:
                if isinstance(a, dict):
                    text = a.get("text", "").strip()
                    src = a.get("source_note", stem)
                else:
                    text = str(a).strip()
                    src = stem
                if not text:
                    continue
                line = f"- {prefix}{text} → [[{src}|Source]]"
                (owner_lines if f"[[{OWNER_SLUG}]]" in text else other_lines).append(line)

    if not owner_lines and not other_lines:
        return []

    lines = ["## Action items", ""]
    for label, bucket in (("Sam-owned", owner_lines), ("Waiting on others", other_lines)):
        if not bucket:
            continue
        lines.append(f"**{label}**")
        lines.extend(bucket[:ACTIONS_CAP])
        overflow = len(bucket) - ACTIONS_CAP
        if overflow > 0:
            lines.append(f"- …and {overflow} more in source notes")
        lines.append("")
    return lines


def render_attention(override_bullets: list[str]) -> list[str]:
    if not override_bullets:
        return []
    lines = ["## Attention needed", ""]
    for b in override_bullets:
        lines.append(f"- {b}")
    lines.append("")
    return lines


def render_ingestion_summary(emails: list[dict], meetings: list[dict], docs: list[dict] | None = None) -> list[str]:
    docs = docs or []
    email_count = len(emails)
    email_high = sum(1 for e in emails if e.get("relevance") == "high")
    email_medium = sum(1 for e in emails if e.get("relevance") == "medium")
    email_low = sum(1 for e in emails if e.get("relevance") == "low")
    meeting_count = len(meetings)
    doc_count = len(docs)
    parts = []
    if email_count:
        parts.append(f"{email_count} email{'s' if email_count != 1 else ''} ({email_high} high, {email_medium} medium, {email_low} low)")
    if meeting_count:
        parts.append(f"{meeting_count} meeting{'s' if meeting_count != 1 else ''}")
    if doc_count:
        parts.append(f"{doc_count} reference doc{'s' if doc_count != 1 else ''}")
    if not parts:
        return []
    summary = "Ingested " + ", ".join(parts) + "."
    return ["## Ingestion summary", "", summary, ""]


# ---------------------------------------------------------------------------
# Daily note assembly
# ---------------------------------------------------------------------------

def build_briefing(entries: list[dict], overrides: dict | None = None,
                   target_date: str | None = None, vault: Path | None = None) -> str:
    """Build the full briefing block (between H1 and `## Today's focus`).

    Emails are distinguished from meetings via the note_path filename pattern
    `YYYY-MM-DD-email-*.md`. Transcript-processor also sets `meeting_type`, but
    the filename check is authoritative and works even if agents drop the field.
    """
    emails = []
    meetings = []
    docs = []
    for e in entries:
        nm = Path(e.get("note_path", "")).name
        note_path = e.get("note_path", "")
        if "-email-" in nm:
            emails.append(e)
        elif e.get("type") in {"reference", "doc"} or str(note_path).startswith("08-Reference/"):
            docs.append(e)
        else:
            meetings.append(e)

    sections = []
    sections.extend(render_meetings(meetings))
    sections.extend(render_key_emails(emails))
    sections.extend(render_reference_docs(docs))
    sections.extend(render_decisions(entries))
    sections.extend(render_action_items(entries, vault=vault))
    if overrides:
        sections.extend(render_attention(overrides.get("attention_needed", [])))
    sections.extend(render_ingestion_summary(emails, meetings, docs))

    # Sign-off
    sign_off = None
    if overrides:
        sign_off = overrides.get("sign_off")
    sections.append("---")
    sections.append("")
    if sign_off:
        sections.append(f"*{sign_off}*")
    else:
        sections.append("*Morning scan complete.*")
    sections.append("")
    return "\n".join(sections)


def build_new_daily_note(target_date: str, briefing: str) -> str:
    d = date.fromisoformat(target_date)
    week = d.isocalendar()[1]
    day_name = d.strftime("%A")
    month_name = d.strftime("%B")

    fm = [
        "---",
        f"date: {target_date}",
        "type: daily",
        f"week: {week}",
        "---",
        "",
        f"# {day_name}, {month_name} {d.day:02d}",
        "",
    ]
    lines = fm
    if briefing:
        lines.append(briefing.rstrip() + "\n")
    lines.extend([
        "## Today's focus",
        "1.",
        "2.",
        "3.",
        "",
        "## Notes",
        "",
    ])
    return "\n".join(lines)


_H1_RE = re.compile(r"^# .+$", re.MULTILINE)
_TODAYS_FOCUS_RE = re.compile(r"^## Today's focus", re.MULTILINE)


def merge_briefing_into_existing(existing: str, briefing: str) -> str:
    """Replace content between the first H1 and `## Today's focus` with the new briefing.

    Preserves frontmatter, the H1 line itself, and everything from `## Today's focus` onward.
    """
    # Find H1
    h1 = _H1_RE.search(existing)
    if not h1:
        return existing  # no H1, don't touch
    # Find `## Today's focus`
    tf = _TODAYS_FOCUS_RE.search(existing)
    if not tf:
        # No focus section, append briefing after H1
        head = existing[: h1.end()] + "\n\n"
        tail = existing[h1.end():]
        return head + briefing.rstrip() + "\n\n" + tail.lstrip("\n")
    # Replace middle chunk
    head = existing[: h1.end()] + "\n\n"
    tail = existing[tf.start():]
    return head + briefing.rstrip() + "\n\n" + tail


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Build daily note briefings from aggregated briefing_data")
    parser.add_argument("--vault", default=str(Path(__file__).resolve().parent.parent),
                        help="Vault root directory")
    parser.add_argument("--inputs", nargs="+", required=True,
                        help="One or more JSON files containing briefing_data (typically _db/email-out.json _db/transcript-out.json)")
    parser.add_argument("--target-date", default=None,
                        help="Today's date (YYYY-MM-DD). Only this date's note gets overrides applied and sign-off.")
    parser.add_argument("--overrides", default=None,
                        help="Optional JSON file with per-date overrides (sign_off, attention_needed)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    vault = Path(args.vault)

    entries = load_briefing_data(args.inputs)
    grouped = group_by_date(entries)

    overrides = {}
    if args.overrides:
        ov_path = Path(args.overrides)
        if ov_path.exists():
            try:
                with open(ov_path, "r", encoding="utf-8") as f:
                    overrides = json.load(f)
            except Exception as e:
                print(f"Warning: could not parse overrides {args.overrides}: {e}", file=sys.stderr)

    result = {"written": [], "updated": [], "skipped": [], "errors": []}

    # Ensure target-date gets a note even if no briefing content exists for it
    if args.target_date and args.target_date not in grouped:
        grouped[args.target_date] = []

    for target_date, day_entries in sorted(grouped.items()):
        try:
            year = target_date[:4]
            note_path = vault / "01-Daily" / year / f"{target_date}.md"
            note_path.parent.mkdir(parents=True, exist_ok=True)

            day_overrides = overrides.get(target_date)
            briefing = build_briefing(day_entries, day_overrides, target_date=target_date, vault=vault)

            # Empty-input safety: with no entries and no meaningful overrides the
            # briefing is just the boilerplate sign-off. Merging that into an
            # existing note would wipe a real briefing (audit finding #1). Only
            # the "ensure target-date gets a note" path (line ~408) hits this,
            # so create a missing note, but never overwrite an existing one.
            has_content = bool(day_entries) or bool(
                day_overrides and (day_overrides.get("attention_needed") or day_overrides.get("sign_off"))
            )

            rel_path = str(note_path.relative_to(vault))
            if note_path.exists():
                if not has_content:
                    result["skipped"].append(rel_path)
                    continue
                existing = note_path.read_text(encoding="utf-8")
                new_content = merge_briefing_into_existing(existing, briefing)
                if args.dry_run:
                    print(f"[dry-run] would update {rel_path}", file=sys.stderr)
                else:
                    atomic_text_write(note_path, new_content)
                result["updated"].append(rel_path)
            else:
                new_content = build_new_daily_note(target_date, briefing)
                if args.dry_run:
                    print(f"[dry-run] would write {rel_path}", file=sys.stderr)
                else:
                    atomic_text_write(note_path, new_content)
                result["written"].append(rel_path)
        except Exception as e:
            result["errors"].append(f"{target_date}: {e}")

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
