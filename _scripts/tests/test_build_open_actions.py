"""Tests for build-open-actions.py demoted-task handling."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib.util
spec = importlib.util.spec_from_file_location(
    "build_open_actions",
    Path(__file__).resolve().parents[1] / "build-open-actions.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_demoted_plain_bullet_not_counted_as_open(tmp_path):
    note = tmp_path / "2026-05-13-meeting.md"
    note.write_text(
        "---\ndate: 2026-05-13\ntype: meeting\n---\n\n"
        "## Actions\n\n"
        "- [ ] [[Sam-Rivera]] real task with deck [created:: 2026-05-13]\n"
        "- [[Sam-Rivera]] demoted artifact [demoted:: forgettability] [created:: 2026-05-13]\n"
    )
    open_actions, completed = mod.scan_file(note, tmp_path)
    assert len(open_actions) == 1
    assert open_actions[0]["description"].startswith("real task")
    assert all("demoted" not in (a.get("description") or "") for a in open_actions)


def test_demoted_with_checkbox_defensive_skip(tmp_path):
    # Defensive case: somehow a checkbox + [demoted::] survives. Must not count.
    note = tmp_path / "2026-05-13-meeting.md"
    note.write_text(
        "---\ndate: 2026-05-13\ntype: meeting\n---\n\n"
        "## Actions\n\n"
        "- [ ] [[Sam-Rivera]] should-be-demoted [demoted:: forgettability] [created:: 2026-05-13]\n"
    )
    open_actions, completed = mod.scan_file(note, tmp_path)
    assert len(open_actions) == 0


def test_demoted_actions_returned_separately(tmp_path):
    note = tmp_path / "2026-05-13-meeting.md"
    note.write_text(
        "---\ndate: 2026-05-13\ntype: meeting\n---\n\n"
        "## Actions\n\n"
        "- [[Sam-Rivera]] read PRD [demoted:: forgettability] [created:: 2026-05-13]\n"
    )
    open_actions, completed, demoted = mod.scan_file_with_demoted(note, tmp_path)
    assert len(open_actions) == 0
    assert len(demoted) == 1
    assert demoted[0]["owner"] == "Sam-Rivera"
    assert demoted[0]["demoted_reason"] == "forgettability"


def test_total_demoted_in_output_json(tmp_path, monkeypatch):
    # Set up minimal vault structure
    interactions = tmp_path / "05-Interactions" / "2026"
    interactions.mkdir(parents=True)
    (interactions / "2026-05-13-test.md").write_text(
        "---\ndate: 2026-05-13\ntype: meeting\n---\n\n## Actions\n\n"
        "- [ ] [[Sam-Rivera]] real task with deck [created:: 2026-05-13]\n"
        "- [[Sam-Rivera]] demoted [demoted:: forgettability] [created:: 2026-05-13]\n"
    )
    db = tmp_path / "_db"
    db.mkdir()

    monkeypatch.setattr(sys, "argv", ["build-open-actions.py", "--vault", str(tmp_path)])
    mod.main()

    output = json.loads((db / "open-actions.json").read_text())
    assert output["total_open"] == 1
    assert output["total_demoted"] == 1
    assert "demoted_actions" in output
    assert len(output["demoted_actions"]) == 1


def test_scans_07_areas_my_tasks(tmp_path, monkeypatch):
    """07-Areas/My-Tasks.md should be scanned for open tasks."""
    interactions = tmp_path / "05-Interactions" / "2026"
    interactions.mkdir(parents=True)
    (interactions / "2026-05-13-test.md").write_text(
        "---\ndate: 2026-05-13\ntype: meeting\n---\n\n## Actions\n\n"
        "- [ ] [[Sam-Rivera]] interaction task [created:: 2026-05-13]\n"
    )
    areas = tmp_path / "07-Areas"
    areas.mkdir()
    (areas / "My-Tasks.md").write_text(
        "---\ndate: 2026-05-15\ntype: personal-tasks\n---\n\n# My Tasks\n\n## Open\n\n"
        "- [ ] [[Sam-Rivera]] personal task [created:: 2026-05-15]\n"
    )
    db = tmp_path / "_db"
    db.mkdir()

    monkeypatch.setattr(sys, "argv", ["build-open-actions.py", "--vault", str(tmp_path)])
    mod.main()

    output = json.loads((db / "open-actions.json").read_text())
    assert output["total_open"] == 2
    descriptions = [a["description"] for a in output["by_owner"]["Sam-Rivera"]]
    assert any("personal task" in d for d in descriptions)
    assert any("interaction task" in d for d in descriptions)


def test_my_tasks_only_when_file_exists(tmp_path, monkeypatch):
    """No My-Tasks.md → no error, just skipped."""
    interactions = tmp_path / "05-Interactions" / "2026"
    interactions.mkdir(parents=True)
    (interactions / "x.md").write_text(
        "---\ndate: 2026-05-13\n---\n\n- [ ] [[Sam-Rivera]] task [created:: 2026-05-13]\n"
    )
    db = tmp_path / "_db"
    db.mkdir()
    monkeypatch.setattr(sys, "argv", ["build-open-actions.py", "--vault", str(tmp_path)])
    mod.main()  # must not raise
    output = json.loads((db / "open-actions.json").read_text())
    assert output["total_open"] == 1
