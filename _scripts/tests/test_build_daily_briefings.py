"""Tests for build-daily-briefings.py empty-input safety.

A no-ingestion / backlog re-run forces the target date into the briefing loop
with zero entries (build-daily-briefings.py: "ensure target-date gets a note").
Without a guard, build_briefing([]) yields only the boilerplate sign-off and
merge_briefing_into_existing() replaces a real existing briefing with it,
wiping meetings, key emails, and actions. The guard must skip updating an
existing note when the new briefing has no real content, while still creating
a missing note and still updating when content (entries or overrides) exists.
"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "build-daily-briefings.py"
TARGET = "2026-06-01"

EXISTING_NOTE = """---
date: 2026-06-01
type: daily
week: 23
---

# Monday, June 01

## Meetings today (1)

- Synced on Q3 roadmap → [[2026-06-01-1on1-jordan|note]]

## Action items

- [ ] [[Sam-Rivera]] send the partnership deck

---

*Morning scan complete.*

## Today's focus
1. ship the MVP

## Notes
pre-existing user notes
"""


def _run(vault: Path, inputs: dict, overrides: dict | None = None):
    inp = vault / "in.json"
    inp.write_text(json.dumps(inputs), encoding="utf-8")
    cmd = [sys.executable, str(SCRIPT), "--vault", str(vault),
           "--inputs", str(inp), "--target-date", TARGET]
    if overrides is not None:
        ov = vault / "overrides.json"
        ov.write_text(json.dumps(overrides), encoding="utf-8")
        cmd += ["--overrides", str(ov)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _make_existing(tmp_path: Path) -> Path:
    daily = tmp_path / "01-Daily" / "2026"
    daily.mkdir(parents=True)
    note = daily / f"{TARGET}.md"
    note.write_text(EXISTING_NOTE, encoding="utf-8")
    return note


def test_empty_run_preserves_existing_briefing(tmp_path):
    """The core bug: empty briefing_data must NOT wipe an existing briefing."""
    note = _make_existing(tmp_path)
    result = _run(tmp_path, {"briefing_data": []})

    content = note.read_text(encoding="utf-8")
    # The real briefing content must survive untouched
    assert "Synced on Q3 roadmap" in content
    assert "send the partnership deck" in content
    # And the carried-forward zones must survive (they always did)
    assert "ship the MVP" in content
    assert "pre-existing user notes" in content
    # The script should report this as skipped, not updated
    assert f"01-Daily/2026/{TARGET}.md" in result["skipped"]
    assert f"01-Daily/2026/{TARGET}.md" not in result["updated"]


def test_empty_run_still_creates_missing_note(tmp_path):
    """Guard must not break the 'ensure target-date gets a note' purpose."""
    (tmp_path / "01-Daily" / "2026").mkdir(parents=True)
    result = _run(tmp_path, {"briefing_data": []})

    note = tmp_path / "01-Daily" / "2026" / f"{TARGET}.md"
    assert note.exists()
    assert f"01-Daily/2026/{TARGET}.md" in result["written"]


def test_real_entries_still_update_existing(tmp_path):
    """Positive control: a run with content still merges into an existing note."""
    note = _make_existing(tmp_path)
    entry = {
        "date": TARGET,
        "note_path": "05-Interactions/2026/2026-06-01-sync-orion.md",
        "summary": "Aligned on Orion MVP scope",
    }
    result = _run(tmp_path, {"briefing_data": [entry]})

    content = note.read_text(encoding="utf-8")
    assert "Aligned on Orion MVP scope" in content
    assert f"01-Daily/2026/{TARGET}.md" in result["updated"]


def test_overrides_only_still_update_existing(tmp_path):
    """An override (attention/sign-off) is real content, must still write."""
    note = _make_existing(tmp_path)
    result = _run(tmp_path, {"briefing_data": []},
                  overrides={TARGET: {"attention_needed": ["Budget sign-off pending"]}})

    content = note.read_text(encoding="utf-8")
    assert "Budget sign-off pending" in content
    assert f"01-Daily/2026/{TARGET}.md" in result["updated"]


def _make_interaction(tmp_path: Path, name: str, body_actions: str) -> str:
    """Write an interaction note and return its vault-relative path."""
    d = tmp_path / "05-Interactions" / "2026"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(
        f"---\ndate: {TARGET}\ntype: meeting\ninteraction-type: meeting\n---\n\n"
        f"# {name}\n\n## Actions\n{body_actions}\n",
        encoding="utf-8")
    return f"05-Interactions/2026/{name}.md"


def test_daily_actions_come_from_final_note_not_briefing_data(tmp_path):
    """Item 4: demoted/completed tasks in the source note must NOT leak into the
    daily, even though briefing_data.actions still carries the demoted copy."""
    src = "2026-06-01-sync"
    rel = _make_interaction(tmp_path, src,
        f"- [ ] [[Sam-Rivera]] real sam task [source:: [[{src}]]]\n"
        f"- [ ] [[Raj-Patel]] real other task [source:: [[{src}]]]\n"
        f"- [[Sam-Rivera]] demoted task [demoted:: forgettability] [source:: [[{src}]]]\n"
        f"- [x] [[Sam-Rivera]] completed task\n")
    note = _make_existing(tmp_path)
    entry = {"date": TARGET, "note_path": rel, "summary": "sync",
             # briefing_data still carries the demoted task (the pre-hygiene leak)
             "actions": [{"text": "[[Sam-Rivera]] demoted task", "source_note": src}]}
    _run(tmp_path, {"briefing_data": [entry]})

    content = note.read_text(encoding="utf-8")
    assert "real sam task" in content
    assert "real other task" in content
    assert "demoted task" not in content      # excluded: read from final note, not briefing_data
    assert "completed task" not in content     # excluded: completed is not open
    assert "**Sam-owned**" in content
    assert "**Waiting on others**" in content


def test_daily_actions_are_capped_with_overflow(tmp_path):
    """Item 6: >5 Sam-owned actions render 5 + an overflow line, never silently truncated."""
    src = "2026-06-01-big"
    lines = "".join(
        f"- [ ] [[Sam-Rivera]] task number {i} [source:: [[{src}]]]\n" for i in range(7))
    rel = _make_interaction(tmp_path, src, lines)
    note = _make_existing(tmp_path)
    _run(tmp_path, {"briefing_data": [{"date": TARGET, "note_path": rel, "summary": "big"}]})

    content = note.read_text(encoding="utf-8")
    assert "…and 2 more in source notes" in content
    # only 5 of the 7 numbered tasks should be rendered
    rendered = sum(1 for i in range(7) if f"task number {i}" in content)
    assert rendered == 5
