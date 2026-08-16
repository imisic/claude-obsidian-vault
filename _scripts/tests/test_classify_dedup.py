"""Tests for classify-inbox.py email content-dedup (cross-filename duplicates).

The bug: load_ingest_log promised a by_key index but only built by_source, so
check_already_processed dedup'd on the Power-Automate filename only. A re-pulled
email under a different filename created a duplicate note.

The fix uses the log for cheap (subject, date) candidate filtering, then confirms
a real duplicate by comparing RESOLVED recipient wikilinks against the candidate
note's frontmatter recipients. Recipient sets must be EQUAL and non-empty to dedup
(no-false-positive guard: distinct emails sharing subject+date must NOT be dropped).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib.util
spec = importlib.util.spec_from_file_location(
    "classify_inbox",
    Path(__file__).resolve().parents[1] / "classify-inbox.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

from utils import recipient_set


# --- recipient_set helper -------------------------------------------------

def test_recipient_set_normalizes_wikilinks():
    s = recipient_set(["[[Mia-Fischer]]", "  [[Jordan-Lee]]  "])
    assert s == frozenset({"mia-fischer", "jordan-lee"})


def test_recipient_set_drops_empties_and_is_order_insensitive():
    assert recipient_set(["[[A]]", "", "  ", "[[B]]"]) == recipient_set(["[[B]]", "[[A]]"])
    assert recipient_set([]) == frozenset()


def test_recipient_set_case_insensitive_equality():
    assert recipient_set(["[[Mia-Fischer]]"]) == recipient_set(["mia-fischer"])


def _write_log(vault: Path, entries: list[dict]) -> None:
    db = vault / "_db"
    db.mkdir(exist_ok=True)
    (db / "ingest-log.json").write_text(json.dumps(entries), encoding="utf-8")


def _write_note(vault: Path, rel_path: str, *, subject: str, date: str,
                to: list[str], cc: list[str] | None = None) -> None:
    note = vault / rel_path
    note.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"date: {date}", "type: email", "interaction-type: email",
             f"subject: {subject}", "to:"]
    for w in to:
        lines.append(f"  - \"{w}\"")
    if cc:
        lines.append("cc:")
        for w in cc:
            lines.append(f"  - \"{w}\"")
    lines += ["---", "", "Body text.", ""]
    note.write_text("\n".join(lines), encoding="utf-8")


def _email_meta(*, filename: str, subject: str, date: str,
                to: list[str], cc: list[str] | None = None) -> dict:
    """Build an email_meta as it looks AT the dedup call site: recipients
    resolved to wikilinks in resolved_to/resolved_cc (resolve_participants ran)."""
    return {
        "filename": filename,
        "subject": subject,
        "date": date,
        "resolved_from": {"wikilink": "[[Sam-Rivera]]", "resolved": True},
        "resolved_to": [{"wikilink": w, "resolved": True} for w in to],
        "resolved_cc": [{"wikilink": w, "resolved": True} for w in (cc or [])],
    }


# --- load_ingest_log: by_key construction ---------------------------------

def test_by_key_built_from_created_email_entry(tmp_path):
    _write_log(tmp_path, [
        {"source-file": "a.txt", "action": "created", "type": "email",
         "subject": "Re: Q2 planning", "date": "2026-06-10",
         "output-file": "05-Interactions/2026/x.md"},
        {"source-file": "b.txt", "action": "skipped-low-relevance", "type": "email",
         "subject": "Lunch?", "date": "2026-06-10", "output-file": None},
        {"source-file": "c.txt", "action": "created", "type": "meeting",
         "subject": "Steerco", "date": "2026-06-10",
         "output-file": "05-Interactions/2026/m.md"},
    ])
    log = mod.load_ingest_log(tmp_path)
    assert "by_source" in log and "by_key" in log
    # Normalized subject strips "Re: "
    key = f"{mod.normalize_subject('Re: Q2 planning')}|2026-06-10"
    assert key in log["by_key"]
    assert len(log["by_key"][key]) == 1
    assert log["by_key"][key][0]["source-file"] == "a.txt"
    # Skipped email is excluded
    assert f"{mod.normalize_subject('Lunch?')}|2026-06-10" not in log["by_key"]
    # Non-email created entry is excluded
    assert f"{mod.normalize_subject('Steerco')}|2026-06-10" not in log["by_key"]


def test_load_ingest_log_missing_file_returns_both_keys(tmp_path):
    log = mod.load_ingest_log(tmp_path)  # no _db/ingest-log.json
    assert log == {"by_source": {}, "by_key": {}}


# --- check_already_processed: content dedup -------------------------------

def test_cross_filename_duplicate_detected(tmp_path):
    _write_note(tmp_path, "05-Interactions/2026/q2.md",
                subject="Q2 planning", date="2026-06-10",
                to=["[[Mia-Fischer]]"], cc=["[[Jordan-Lee]]"])
    _write_log(tmp_path, [
        {"source-file": "original.txt", "action": "created", "type": "email",
         "subject": "Q2 planning", "date": "2026-06-10",
         "output-file": "05-Interactions/2026/q2.md"},
    ])
    log = mod.load_ingest_log(tmp_path)
    # Re-pulled under a DIFFERENT filename, same subject/date/recipients
    meta = _email_meta(filename="REPULL-2026-06-10.txt",
                       subject="Re: Q2 planning", date="2026-06-10",
                       to=["[[Mia-Fischer]]"], cc=["[[Jordan-Lee]]"])
    reason = mod.check_already_processed(meta, log, tmp_path)
    assert reason
    assert "q2.md" in reason


def test_different_recipients_same_subject_date_not_deduped(tmp_path):
    # DATA-LOSS GUARD: distinct emails sharing subject+date must NOT be dropped.
    _write_note(tmp_path, "05-Interactions/2026/q2.md",
                subject="Q2 planning", date="2026-06-10",
                to=["[[Mia-Fischer]]"])
    _write_log(tmp_path, [
        {"source-file": "original.txt", "action": "created", "type": "email",
         "subject": "Q2 planning", "date": "2026-06-10",
         "output-file": "05-Interactions/2026/q2.md"},
    ])
    log = mod.load_ingest_log(tmp_path)
    meta = _email_meta(filename="other.txt", subject="Q2 planning",
                       date="2026-06-10", to=["[[Morgan-Hayes]]"])
    assert mod.check_already_processed(meta, log, tmp_path) is None


def test_empty_recipient_sets_not_deduped(tmp_path):
    # Both sides empty → equal-but-empty must NOT dedup (guard against junk match).
    _write_note(tmp_path, "05-Interactions/2026/q2.md",
                subject="Q2 planning", date="2026-06-10", to=[])
    _write_log(tmp_path, [
        {"source-file": "original.txt", "action": "created", "type": "email",
         "subject": "Q2 planning", "date": "2026-06-10",
         "output-file": "05-Interactions/2026/q2.md"},
    ])
    log = mod.load_ingest_log(tmp_path)
    meta = _email_meta(filename="other.txt", subject="Q2 planning",
                       date="2026-06-10", to=[])
    assert mod.check_already_processed(meta, log, tmp_path) is None


def test_ghost_entry_output_missing_not_deduped(tmp_path):
    # created entry but output note never written → not a content dup.
    _write_log(tmp_path, [
        {"source-file": "original.txt", "action": "created", "type": "email",
         "subject": "Q2 planning", "date": "2026-06-10",
         "output-file": "05-Interactions/2026/ghost.md"},
    ])
    log = mod.load_ingest_log(tmp_path)
    meta = _email_meta(filename="other.txt", subject="Q2 planning",
                       date="2026-06-10", to=["[[Mia-Fischer]]"])
    assert mod.check_already_processed(meta, log, tmp_path) is None


def test_filename_match_by_source_unchanged(tmp_path):
    # by_source check stays FIRST and returns its reason unchanged.
    _write_note(tmp_path, "05-Interactions/2026/q2.md",
                subject="Q2 planning", date="2026-06-10",
                to=["[[Mia-Fischer]]"])
    _write_log(tmp_path, [
        {"source-file": "same.txt", "action": "created", "type": "email",
         "subject": "Q2 planning", "date": "2026-06-10",
         "output-file": "05-Interactions/2026/q2.md"},
    ])
    log = mod.load_ingest_log(tmp_path)
    meta = _email_meta(filename="same.txt", subject="Q2 planning",
                       date="2026-06-10", to=["[[Mia-Fischer]]"])
    reason = mod.check_already_processed(meta, log, tmp_path)
    assert reason == "already-processed"


def test_filename_match_skipped_reason_unchanged(tmp_path):
    _write_log(tmp_path, [
        {"source-file": "low.txt", "action": "skipped-low-relevance",
         "type": "email", "subject": "FYI", "date": "2026-06-10",
         "output-file": None},
    ])
    log = mod.load_ingest_log(tmp_path)
    meta = _email_meta(filename="low.txt", subject="FYI", date="2026-06-10",
                       to=["[[Mia-Fischer]]"])
    assert mod.check_already_processed(meta, log, tmp_path) == "already-skipped-low-relevance"
