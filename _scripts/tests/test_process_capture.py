"""Tests for process-capture.py: daily-note ## Capture routing."""
import json
import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib.util
spec = importlib.util.spec_from_file_location(
    "process_capture",
    Path(__file__).resolve().parents[1] / "process-capture.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _make_daily_note(tmp_path, capture_lines):
    """Helper to create a daily note with given Capture content."""
    daily = tmp_path / "01-Daily" / "2026" / "2026-05-15.md"
    daily.parent.mkdir(parents=True)
    body = (
        "---\ndate: 2026-05-15\ntype: daily\nweek: 20\n---\n\n"
        "# Friday, May 15\n\n"
        "## Today's focus\n1.\n2.\n3.\n\n"
        "## Capture\n"
        "<!-- comment -->\n"
        + "\n".join(capture_lines) + "\n"
        "\n## Notes\n\nexisting notes content\n"
    )
    daily.write_text(body)
    return daily


def test_capture_checkbox_routes_to_my_tasks(tmp_path):
    daily = _make_daily_note(tmp_path, ["- [ ] follow up with HR on bonus"])
    mod.process(tmp_path, daily, target_date="2026-05-15")

    my_tasks = tmp_path / "07-Areas" / "My-Tasks.md"
    assert my_tasks.exists()
    content = my_tasks.read_text()
    assert "## Open" in content
    assert "follow up with HR on bonus" in content
    assert "[[Sam-Rivera]]" in content  # owner inferred
    assert "[created:: 2026-05-15]" in content
    assert "[source:: [[2026-05-15]]]" in content


def test_capture_plain_bullet_routes_to_notes(tmp_path):
    daily = _make_daily_note(tmp_path, ["- random observation about Q3"])
    mod.process(tmp_path, daily, target_date="2026-05-15")

    content = daily.read_text()
    assert "random observation about Q3" in content
    # Should appear under ## Notes
    notes_section = content.split("## Notes")[1]
    assert "random observation about Q3" in notes_section


def test_capture_section_cleared_after_processing(tmp_path):
    daily = _make_daily_note(tmp_path, [
        "- [ ] task one",
        "- plain note one",
    ])
    mod.process(tmp_path, daily, target_date="2026-05-15")

    content = daily.read_text()
    capture_block = content.split("## Capture")[1].split("## Notes")[0]
    # Comments preserved, actual content stripped
    assert "<!--" in capture_block or "comment" in capture_block
    assert "task one" not in capture_block
    assert "plain note one" not in capture_block


def test_capture_empty_section_skipped(tmp_path):
    daily = _make_daily_note(tmp_path, [])
    daily_before = daily.read_text()
    result = mod.process(tmp_path, daily, target_date="2026-05-15")

    assert result["processed_lines"] == 0
    my_tasks = tmp_path / "07-Areas" / "My-Tasks.md"
    assert not my_tasks.exists()
    # Daily note untouched
    assert daily.read_text() == daily_before


def test_capture_owner_explicit_preserved(tmp_path):
    # If user typed [[SomeoneElse]] explicitly, don't override with Sam
    daily = _make_daily_note(tmp_path, ["- [ ] [[Mia-Fischer]] book a venue"])
    mod.process(tmp_path, daily, target_date="2026-05-15")

    my_tasks = (tmp_path / "07-Areas" / "My-Tasks.md").read_text()
    assert "[[Mia-Fischer]]" in my_tasks
    assert "[[Sam-Rivera]]" not in my_tasks
