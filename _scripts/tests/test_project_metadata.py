"""Tests for audit-project-metadata.py: the `project:` frontmatter resolver.

Two properties carry the whole design and are what these tests defend.

The resolver scores signal DOMINANCE, not keyword presence: a note about your
domain that mentions an out-of-domain term once must still resolve, while a
note that is mostly out-of-domain must be suppressed even when a domain word
appears. A presence-based matcher gets both of those backwards.

And the fix path is deliberately narrow: MISMATCH, MISSING-HIGH-CONFIDENCE and
EMPTY/MALFORMED are applied, everything else is reported. UNCERTAIN existing at
all is the point, so a test that lets --fix touch it is a real regression.

The signal config ships empty, so every test here installs its own via
monkeypatch. Fixtures use the template's own roster and project notes.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_spec = importlib.util.spec_from_file_location(
    "audit_project_metadata",
    Path(__file__).resolve().parents[1] / "audit-project-metadata.py",
)
apm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apm)

VALID = {"Project-Alpha", "Project-Beta", "Partner-Onboarding"}


@pytest.fixture
def configured(monkeypatch):
    """A worked config: Orion is the primary domain, Nimbus the old one."""
    monkeypatch.setattr(apm, "ANCHORS", [r"\borion\b", r"\bpilot\b", r"\breadiness\b"])
    monkeypatch.setattr(apm, "HARD_GUARDS", [r"\bnimbus\s*sunset\b"])
    monkeypatch.setattr(apm, "SOFT_GUARDS", [r"\bnimbus\b"])
    monkeypatch.setattr(apm, "SECONDARY_PROJECTS",
                        {"Partner-Onboarding": ([r"\bpartner\s*onboarding\b"], "high")})
    monkeypatch.setattr(apm, "SUBPROJECT_KEYWORDS",
                        {"Project-Alpha": {"Project-Beta": [r"\bbeta\s*track\b"]}})
    monkeypatch.setattr(apm, "PRIMARY_PARENT", "Project-Alpha")
    monkeypatch.setattr(apm, "CROSS_CUTTING_SIGNALS",
                        [r"\borion\b", r"\bnimbus\b", r"\bnorthwind\b"])
    monkeypatch.setattr(apm, "DRIFT_ALIASES", {"Alpha-Programme": "Project-Alpha"})


def note(body, project=None):
    """Build a note; project=None omits the line, '' writes an empty one."""
    line = "" if project is None else f'project: "[[{project}]]"\n' if project else "project:\n"
    return f"---\ndate: 2026-03-04\ntype: meeting\n{line}---\n\n{body}\n"


def write(tmp_path, body, project=None, name="2026-03-04-sync.md"):
    vault = tmp_path
    (vault / "03-Projects").mkdir(parents=True, exist_ok=True)
    for slug in VALID:
        (vault / "03-Projects" / f"{slug}.md").write_text("---\ntype: project\n---\n")
    d = vault / "05-Interactions" / "2026"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(note(body, project), encoding="utf-8")
    return p


# ---------- dominance ----------

def test_anchors_win_over_a_single_hard_guard_mention(configured):
    """The headline property: one out-of-domain mention must not veto."""
    slug, conf, _ = apm.resolve("Orion pilot readiness, and the Nimbus sunset came up once.")
    assert slug == "Project-Alpha"
    assert conf == "high"


def test_hard_guard_vetoes_when_it_matches_the_anchor_count(configured):
    slug, conf, _ = apm.resolve("Nimbus sunset handover. Orion mentioned in passing.")
    assert slug is None
    assert conf == "high"


def test_soft_guard_suppresses_only_with_zero_anchors(configured):
    slug, conf, _ = apm.resolve("Nimbus roadmap discussion.")
    assert slug is None
    assert conf == "high"


def test_soft_guard_is_inert_once_any_anchor_is_present(configured):
    slug, _, _ = apm.resolve("Nimbus and the Orion pilot.")
    assert slug == "Project-Alpha"


def test_distinct_patterns_counted_once_each(configured):
    """Repeating one word must not inflate the anchor score to high."""
    _, conf, _ = apm.resolve("Orion orion orion orion.")
    assert conf == "uncertain"


# ---------- confidence and sub-projects ----------

def test_three_anchors_reach_high_confidence(configured):
    _, conf, _ = apm.resolve("Orion pilot readiness review.")
    assert conf == "high"


def test_single_subproject_match_wins(configured):
    slug, _, _ = apm.resolve("Orion pilot readiness on the beta track.")
    assert slug == "Project-Beta"


def test_no_subproject_match_falls_back_to_parent(configured):
    slug, _, _ = apm.resolve("Orion pilot readiness review.")
    assert slug == "Project-Alpha"


def test_cross_cutting_note_is_never_auto_tagged(configured):
    slug, conf, reason = apm.resolve("Orion, Nimbus and Northwind portfolio review.")
    assert slug is None
    assert conf == "uncertain"
    assert "cross-cutting" in reason


def test_secondary_project_matches_only_without_an_anchor(configured):
    assert apm.resolve("Partner onboarding kickoff.")[0] == "Partner-Onboarding"
    assert apm.resolve("Partner onboarding during Orion pilot readiness.")[0] == "Project-Alpha"


# ---------- classification ----------

def test_unknown_target_is_reported_not_fixed(tmp_path):
    """Zero config still catches a project: pointing at a note that is gone."""
    p = write(tmp_path, "Routine sync.", project="Project-Renamed")
    r = apm.classify(p, VALID)
    assert r["category"] == "UNKNOWN-TARGET"
    assert r["action"] is None
    assert r["category"] not in apm.SAFE_TO_FIX


def test_missing_line_backfilled_at_high_confidence(configured, tmp_path):
    p = write(tmp_path, "Orion pilot readiness review.")
    r = apm.classify(p, VALID)
    assert r["category"] == "MISSING-HIGH-CONFIDENCE"
    assert r["action"] == ("add", "Project-Alpha")


def test_missing_line_stays_uncertain_below_the_threshold(configured, tmp_path):
    p = write(tmp_path, "Orion came up.")
    r = apm.classify(p, VALID)
    assert r["category"] == "UNCERTAIN"
    assert r["action"] is None


def test_parent_child_disagreement_is_not_a_mismatch(configured, tmp_path):
    """A finer tag than the resolver's is a refinement, not a contradiction."""
    p = write(tmp_path, "Orion pilot readiness review.", project="Project-Beta")
    assert apm.classify(p, VALID)["category"] == "OK"


def test_cross_family_disagreement_is_a_mismatch(configured, tmp_path):
    p = write(tmp_path, "Orion pilot readiness review.", project="Partner-Onboarding")
    r = apm.classify(p, VALID)
    assert r["category"] == "MISMATCH"
    assert r["action"] == ("set", "Project-Alpha")


def test_drift_alias_is_retagged(configured, tmp_path):
    p = write(tmp_path, "Orion pilot readiness review.", project="Alpha-Programme")
    r = apm.classify(p, VALID)
    assert r["category"] == "MISMATCH"
    assert r["action"] == ("set", "Project-Alpha")


def test_tagged_note_whose_content_is_out_of_domain_loses_the_tag(configured, tmp_path):
    p = write(tmp_path, "Nimbus sunset handover.", project="Project-Alpha")
    r = apm.classify(p, VALID)
    assert r["category"] == "MISMATCH"
    assert r["action"] == "remove"


def test_empty_line_removed_when_nothing_resolves(configured, tmp_path):
    p = write(tmp_path, "Routine catch-up.", project="")
    r = apm.classify(p, VALID)
    assert r["category"] == "EMPTY/MALFORMED"
    assert r["action"] == "remove"


def test_empty_line_filled_when_confident(configured, tmp_path):
    p = write(tmp_path, "Orion pilot readiness review.", project="")
    r = apm.classify(p, VALID)
    assert r["category"] == "EMPTY/MALFORMED"
    assert r["action"] == ("set", "Project-Alpha")


# ---------- editing ----------

def test_add_inserts_before_the_closing_frontmatter_dashes(configured, tmp_path):
    p = write(tmp_path, "Orion pilot readiness review.")
    apm.apply_action(p, apm.classify(p, VALID))
    lines = p.read_text(encoding="utf-8").splitlines()
    assert lines[lines.index("---", 1) - 1] == 'project: "[[Project-Alpha]]"'


def test_edit_touches_only_the_project_line(configured, tmp_path):
    p = write(tmp_path, "Nimbus sunset handover.", project="Project-Alpha")
    before = p.read_text(encoding="utf-8").splitlines()
    apm.apply_action(p, apm.classify(p, VALID))
    after = p.read_text(encoding="utf-8").splitlines()
    assert [ln for ln in before if not ln.startswith("project:")] == after


def test_uncertain_note_is_left_untouched(configured, tmp_path):
    p = write(tmp_path, "Orion came up.")
    before = p.read_text(encoding="utf-8")
    r = apm.classify(p, VALID)
    assert not apm.apply_action(p, r)
    assert p.read_text(encoding="utf-8") == before


# ---------- unconfigured vault ----------

def test_empty_config_never_backfills(tmp_path):
    """Ships empty: no anchors means no high confidence means no writes."""
    p = write(tmp_path, "Orion pilot readiness review.")
    r = apm.classify(p, VALID)
    assert r["category"] == "OK"
    assert r["action"] is None


def test_empty_config_still_validates_existing_links(tmp_path):
    p = write(tmp_path, "Orion pilot readiness review.", project="Project-Alpha")
    assert apm.classify(p, VALID)["category"] == "OK"
