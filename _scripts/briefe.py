#!/usr/bin/env python3
"""briefe.py: Rebuild daily briefings from notes on disk (v2 single source of truth).

The old flow built briefings from an ephemeral briefing_data snapshot of the
current run. Rebuilding a past day from only that run's inputs wiped that day's
earlier content, including LLM-authored `## Attention needed` bullets and the
italic sign-off (see memory w-daily-second-same-day-run-wipes-briefing).

This script fixes that class structurally: for every date it ALWAYS rebuilds the
briefing from ALL of that date's interaction/reference notes on disk, and it
PRESERVES bespoke content by extracting it from the existing note before the
rebuild. Deterministic sections (meetings, emails, decisions, actions, ingestion
count) are regenerated from the notes; only Attention needed and the sign-off,
which the renderer cannot regenerate, are carried across.

Usage:
    python3 briefe.py --target-date YYYY-MM-DD [--vault PATH]
                      [--touched YYYY-MM-DD ...] [--overrides FILE]

    --touched takes either form: `--touched D1 D2` or `--touched D1 --touched D2`.

Overrides file (JSON), authoritative per-date when present:
    {"2026-07-06": {"sign_off": "line", "attention_needed": ["b1", "b2"]}}

Output (stdout): {"written": [...], "updated": [...], "skipped": [...], "errors": [...]}
"""

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from utils import ensure_utf8_stdio, atomic_text_write  # noqa: E402


def _load(filename: str, modname: str):
    """Load a hyphen-named sibling script as a module (import can't do hyphens)."""
    spec = importlib.util.spec_from_file_location(modname, SCRIPT_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# Reuse the deterministic renderer and the disk-scan collector instead of copying.
_daily = _load("build-daily-briefings.py", "build_daily_briefings")
_rebuild = _load("rebuild-daily-from-notes.py", "rebuild_daily_from_notes")

DEFAULT_SIGN_OFF = "Morning scan complete."
_H1_RE = re.compile(r"^# .+$", re.MULTILINE)
_TODAYS_FOCUS_RE = re.compile(r"^## Today's focus", re.MULTILINE)
# A standalone italic line (single-asterisk wrap), not bold (`**...**`).
_SIGN_OFF_RE = re.compile(r"^\*(?!\*)(.+?)(?<!\*)\*$")


def run_process_capture(vault: Path, target_date: str) -> str | None:
    """Route the target date's ## Capture section before the rebuild.

    Returns a warning string on failure (caller logs and continues). The
    "no Capture section" case exits 0 and is a benign no-op.
    """
    script = SCRIPT_DIR / "process-capture.py"
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--vault", str(vault), "--date", target_date],
            capture_output=True, text=True, cwd=str(vault),
        )
        if proc.returncode != 0:
            return f"process-capture exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()}"
    except Exception as e:
        return f"process-capture failed: {e}"
    return None


def extract_overrides_from_note(existing: str) -> dict:
    """Recover bespoke briefing content the deterministic renderer can't regenerate.

    The briefing block is everything between the H1 and `## Today's focus`. Only
    two touches there are LLM-authored: the `## Attention needed` bullets and the
    italic sign-off line. Pull both so a full rebuild carries them across. A
    sign-off equal to the boilerplate default is treated as absent.
    """
    h1 = _H1_RE.search(existing)
    if not h1:
        return {}
    tf = _TODAYS_FOCUS_RE.search(existing)
    block = existing[h1.end(): tf.start()] if tf else existing[h1.end():]

    overrides: dict = {}
    attention = _rebuild.section_bullets(block, "Attention needed")
    if attention:
        overrides["attention_needed"] = attention

    sign_off = None
    for line in block.splitlines():
        m = _SIGN_OFF_RE.match(line.strip())
        if m:
            sign_off = m.group(1).strip()  # last italic line wins (it trails the ---)
    if sign_off and sign_off != DEFAULT_SIGN_OFF:
        overrides["sign_off"] = sign_off
    return overrides


def rebuild_date(vault: Path, date_str: str, cli_override, year_cache: dict, result: dict) -> None:
    year = date_str[:4]
    if year not in year_cache:
        year_cache[year] = _rebuild.collect_entries(vault, year)
    entries = year_cache[year].get(date_str, [])

    note_path = vault / "01-Daily" / year / f"{date_str}.md"
    rel = str(note_path.relative_to(vault))
    note_exists = note_path.exists()
    existing = note_path.read_text(encoding="utf-8", errors="replace") if note_exists else ""

    # CLI overrides are authoritative when the date is present in the file; else
    # recover the note's own bespoke content so the rebuild never drops it.
    if cli_override is not None:
        day_overrides = cli_override
    elif note_exists:
        day_overrides = extract_overrides_from_note(existing)
    else:
        day_overrides = None

    # A rebuild is warranted only by real disk entries or an explicit CLI override.
    # Extracted bespoke content does NOT count: it comes from the note itself, and
    # letting it trigger a rebuild would strip a zero-entry date's briefing down to
    # just its sign-off. When there is nothing to rebuild from, preserve the note
    # whole (the w-daily-second-same-day-run-wipes-briefing clobber class).
    override_has_content = bool(cli_override and (cli_override.get("attention_needed") or cli_override.get("sign_off")))
    has_content = bool(entries) or override_has_content
    if note_exists and not has_content:
        result["skipped"].append(rel)
        return

    briefing = _daily.build_briefing(entries, day_overrides, target_date=date_str, vault=vault)
    if note_exists:
        new_content = _daily.merge_briefing_into_existing(existing, briefing)
        if new_content == existing:
            result["skipped"].append(rel)
            return
        atomic_text_write(note_path, new_content)
        result["updated"].append(rel)
    else:
        note_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_text_write(note_path, _daily.build_new_daily_note(date_str, briefing))
        result["written"].append(rel)


def main() -> int:
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description="Rebuild daily briefings from notes on disk")
    ap.add_argument("--vault", default=".")
    ap.add_argument("--target-date", required=True, help="Today's date (YYYY-MM-DD)")
    # extend+nargs accepts both `--touched A B` and `--touched A --touched B`: the
    # caller is an LLM reading SKILL.md, and a form mismatch used to abort the whole
    # briefing step on an argparse error.
    ap.add_argument("--touched", action="extend", nargs="+",
                    help="Extra date(s) to rebuild; repeatable and space-separated")
    ap.add_argument("--overrides", default=None, help="JSON: {date: {sign_off, attention_needed}}")
    args = ap.parse_args()

    vault = Path(args.vault).resolve()
    result = {"written": [], "updated": [], "skipped": [], "errors": []}

    warn = run_process_capture(vault, args.target_date)
    if warn:
        print(f"Warning: {warn}", file=sys.stderr)

    cli_overrides: dict = {}
    if args.overrides:
        op = Path(args.overrides)
        if op.exists():
            try:
                cli_overrides = json.loads(op.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"Warning: could not parse overrides {args.overrides}: {e}", file=sys.stderr)

    year_cache: dict = {}
    for date_str in sorted({args.target_date, *(args.touched or [])}):
        try:
            rebuild_date(vault, date_str, cli_overrides.get(date_str), year_cache, result)
        except Exception as e:
            result["errors"].append(f"{date_str}: {e}")

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
