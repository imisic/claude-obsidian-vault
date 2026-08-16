"""Tests for task hygiene helpers in utils.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils import (
    count_audience,
    has_protecting_vip,
    apply_task_hygiene,
    stamp_created,
    parse_task_line,
)

# Mirror what load_vip_slugs() returns for the three protecting-tier slugs
# we actually use in tests. Jordan is boss-chain, Elena is stakeholder.
VIP_SLUGS = {"Jordan-Lee", "Morgan-Hayes", "Patricia-Vance",
             "Elena-Rossi", "David-Klein", "Noah-Bauer", "George-Pappas"}


# ---------- count_audience ----------

def test_count_audience_email_uses_to_plus_cc():
    fm = {"type": "email", "to": ["[[A]]", "[[B]]"], "cc": ["[[C]]"]}
    assert count_audience(fm) == 3


def test_count_audience_email_no_cc():
    fm = {"type": "email", "to": ["[[A]]", "[[B]]"]}
    assert count_audience(fm) == 2


def test_count_audience_meeting_uses_attendees():
    fm = {"type": "meeting", "attendees": ["[[A]]", "[[B]]", "[[C]]"]}
    assert count_audience(fm) == 3


def test_count_audience_missing_returns_one():
    """Malformed note with no attendee data: treat as solo for safety."""
    assert count_audience({"type": "meeting"}) == 1
    assert count_audience({}) == 1


# ---------- has_protecting_vip ----------

def test_has_protecting_vip_boss_chain():
    assert has_protecting_vip({"vip-involved": ["boss-chain"]}) is True


def test_has_protecting_vip_stakeholder():
    assert has_protecting_vip({"vip-involved": ["stakeholder"]}) is True


def test_has_protecting_vip_team_only_is_false():
    """Team-tier VIPs are daily collaborators, don't trigger the gate."""
    assert has_protecting_vip({"vip-involved": ["team"]}) is False


def test_has_protecting_vip_none():
    assert has_protecting_vip({}) is False
    assert has_protecting_vip({"vip-involved": []}) is False


# ---------- stamp_created ----------

def test_stamp_created_appends_when_missing():
    line = "- [ ] [[Jordan-Lee]] schedule intro [source:: [[note]]]"
    out = stamp_created(line, "2026-05-11")
    assert "[created:: 2026-05-11]" in out
    assert "[[Jordan-Lee]]" in out
    assert "[source:: [[note]]]" in out


def test_stamp_created_skips_when_present():
    line = "- [ ] [[Sam-Rivera]] write spec [created:: 2026-04-01]"
    out = stamp_created(line, "2026-05-11")
    assert out == line
    assert out.count("[created::") == 1


def test_stamp_created_handles_completed_task():
    line = "- [x] [[Sam-Rivera]] done thing"
    out = stamp_created(line, "2026-05-11")
    assert "[created:: 2026-05-11]" in out


def test_stamp_created_ignores_non_task_lines():
    line = "Just a regular paragraph mentioning [[someone]]."
    out = stamp_created(line, "2026-05-11")
    assert out == line


# ---------- parse_task_line ----------

def test_parse_task_line_extracts_owner_and_status():
    parsed = parse_task_line("- [ ] [[Jordan-Lee]] do thing")
    assert parsed["owner"] == "Jordan-Lee"
    assert parsed["status"] == "todo"
    assert parsed["is_task"] is True


def test_parse_task_line_detects_delegated_by():
    line = "- [ ] [[Jordan]] do thing [delegated-by:: [[Sam-Rivera]]]"
    parsed = parse_task_line(line)
    assert parsed["delegated_by"] == "Sam-Rivera"


def test_parse_task_line_not_a_task():
    parsed = parse_task_line("- Just a bullet")
    assert parsed["is_task"] is False


# ---------- apply_task_hygiene ----------

SAM = "Sam-Rivera"


def test_hygiene_keeps_owner_owned_unchanged_except_for_created():
    fm = {"type": "meeting", "meeting-type": "general", "attendees": ["[[A]]"] * 9, "date": "2026-05-11"}
    line = "- [ ] [[Sam-Rivera]] dig up the deck"
    out = apply_task_hygiene(line, fm)
    assert "[[Sam-Rivera]]" in out
    assert out.startswith("- [ ]")
    assert "[created:: 2026-05-11]" in out


def test_hygiene_keeps_already_delegated_unchanged_except_for_created():
    fm = {"type": "meeting", "meeting-type": "general", "attendees": ["[[A]]"] * 9, "date": "2026-05-11"}
    line = "- [ ] [[Jordan]] do thing [delegated-by:: [[Sam-Rivera]]]"
    out = apply_task_hygiene(line, fm)
    assert "[delegated-by:: [[Sam-Rivera]]]" in out
    assert out.startswith("- [ ]")
    assert "[created:: 2026-05-11]" in out


def test_hygiene_strips_checkbox_large_meeting_non_owner_no_delegation():
    fm = {"type": "meeting", "meeting-type": "general", "attendees": ["[[A]]"] * 9, "date": "2026-05-11"}
    line = "- [ ] [[Tom-Becker]] do the rollout dependency math"
    out = apply_task_hygiene(line, fm)
    assert not out.lstrip().startswith("- [ ]")
    assert out.lstrip().startswith("- ")
    assert "[[Tom-Becker]]" in out
    assert "[created:: 2026-05-11]" in out


def test_hygiene_auto_delegates_in_1on1():
    fm = {"type": "meeting", "meeting-type": "1on1", "attendees": ["[[Sam-Rivera]]", "[[Jordan]]"], "date": "2026-05-11"}
    line = "- [ ] [[Jordan]] share Q2 numbers"
    out = apply_task_hygiene(line, fm)
    assert "[delegated-by:: [[Sam-Rivera]]]" in out
    assert out.startswith("- [ ]")


def test_hygiene_auto_delegates_in_small_meeting():
    fm = {"type": "meeting", "meeting-type": "general", "attendees": ["[[A]]", "[[B]]", "[[C]]"], "date": "2026-05-11"}
    line = "- [ ] [[B]] do thing"
    out = apply_task_hygiene(line, fm)
    assert "[delegated-by:: [[Sam-Rivera]]]" in out


def test_hygiene_auto_delegates_in_sent_email():
    fm = {"type": "email", "direction": "sent", "to": ["[[Raj]]"], "date": "2026-05-11"}
    line = "- [ ] [[Raj]] respond by Friday"
    out = apply_task_hygiene(line, fm)
    assert "[delegated-by:: [[Sam-Rivera]]]" in out


def test_hygiene_keeps_owed_to_owner_in_received_email_small():
    """Received small email: ambiguous whether asked or volunteered. Keep checkbox, no delegated-by."""
    fm = {"type": "email", "to": ["[[Sam-Rivera]]"], "date": "2026-05-11"}
    line = "- [ ] [[Raj]] send the deck by Friday"
    out = apply_task_hygiene(line, fm)
    assert out.startswith("- [ ]")
    assert "[delegated-by::" not in out
    assert "[created:: 2026-05-11]" in out


def test_hygiene_strips_received_email_broadcast():
    fm = {"type": "email", "to": ["[[Sam-Rivera]]"] + [f"[[X{i}]]" for i in range(6)], "date": "2026-05-11"}
    line = "- [ ] [[Raj]] do something"
    out = apply_task_hygiene(line, fm)
    assert not out.lstrip().startswith("- [ ]")


def test_hygiene_vip_owner_protects_in_large_meeting():
    """Task owned by a boss-chain VIP keeps checkbox even in a >5 meeting."""
    fm = {
        "type": "meeting",
        "meeting-type": "general",
        "attendees": ["[[A]]"] * 9,
        "vip-involved": ["boss-chain"],
        "date": "2026-05-11",
    }
    line = "- [ ] [[Jordan-Lee]] grant SharePoint access to Sam"
    out = apply_task_hygiene(line, fm, vip_slugs=VIP_SLUGS)
    assert out.startswith("- [ ]")
    assert "[delegated-by::" not in out
    assert "[created:: 2026-05-11]" in out


def test_hygiene_stakeholder_owner_protects():
    """Stakeholder VIP (Elena) commitment → keep checkbox regardless of size."""
    fm = {
        "type": "meeting",
        "meeting-type": "general",
        "attendees": ["[[A]]"] * 9,
        "date": "2026-05-11",
    }
    line = "- [ ] [[Elena-Rossi]] review strategy doc by Friday"
    out = apply_task_hygiene(line, fm, vip_slugs=VIP_SLUGS)
    assert out.startswith("- [ ]")
    assert "[delegated-by::" not in out


def test_hygiene_team_vip_owner_does_not_protect():
    """Team-tier VIPs (Mia, Raj, Sofia, Lukas, Piotr, Arun) are
    NOT in vip_slugs, their tasks follow normal size-based rules.
    """
    fm = {
        "type": "meeting",
        "meeting-type": "general",
        "attendees": ["[[A]]"] * 9,
        "date": "2026-05-11",
    }
    line = "- [ ] [[Lukas-Berger]] complete the risk mitigation plan"
    out = apply_task_hygiene(line, fm, vip_slugs=VIP_SLUGS)
    # Lukas is team-tier, not in VIP_SLUGS, large meeting strips it
    assert not out.lstrip().startswith("- [ ]")


def test_hygiene_non_vip_owner_strips_in_large_meeting_even_with_vip_attending():
    """The original bug: Jordan attending doesn't protect Tom's commitments.

    Tom-Becker is NOT in vip_slugs. Even though the note has boss-chain
    in vip-involved (Jordan attended), Tom's task should be stripped.
    A boss-chain attendee must not launder every other attendee's task into the VIP-touched view.
    """
    fm = {
        "type": "meeting",
        "meeting-type": "general",
        "attendees": ["[[A]]"] * 9,
        "vip-involved": ["boss-chain"],
        "date": "2026-05-11",
    }
    line = "- [ ] [[Tom-Becker]] do the rollout dependency math"
    out = apply_task_hygiene(line, fm, vip_slugs=VIP_SLUGS)
    assert not out.lstrip().startswith("- [ ]")
    assert "[created:: 2026-05-11]" in out


def test_hygiene_no_vip_slugs_keeps_old_behavior_for_size_matrix():
    """When vip_slugs is None or empty, the size matrix runs unchanged."""
    fm = {
        "type": "meeting",
        "meeting-type": "general",
        "attendees": ["[[A]]"] * 9,
        "date": "2026-05-11",
    }
    line = "- [ ] [[Random-Person]] do thing"
    out = apply_task_hygiene(line, fm, vip_slugs=None)
    assert not out.lstrip().startswith("- [ ]")


def test_hygiene_non_task_passes_through():
    fm = {"type": "meeting", "attendees": ["[[A]]"], "date": "2026-05-11"}
    line = "Just a discussion bullet"
    out = apply_task_hygiene(line, fm)
    assert out == line


# ---------- passes_forgettability ----------

from utils import passes_forgettability, _demote_with_marker, _task_description_for_forgettability  # noqa: E402

def test_forgettability_horizon_keeps():
    assert passes_forgettability("read the consultant deck before Thursday")

def test_forgettability_iso_date_keeps():
    assert passes_forgettability("attend workshop 2026-05-19")

def test_forgettability_deliverable_noun_keeps():
    assert passes_forgettability("write the Project-Alpha proposal")

def test_forgettability_small_ask_verb_keeps():
    assert passes_forgettability("schedule 1on1 with Jordan")

def test_forgettability_dig_up_keeps():
    assert passes_forgettability("dig up the regional bucket deck and share")

def test_forgettability_blocker_keeps():
    assert passes_forgettability("waiting on Jordan for SharePoint")

def test_forgettability_no_signal_demotes():
    assert not passes_forgettability("read PRD")

def test_forgettability_continue_verb_demotes():
    assert not passes_forgettability("continue removing self from lists")

def test_forgettability_help_verb_demotes():
    assert not passes_forgettability("help manage regional pushback")

def test_forgettability_attend_alone_demotes():
    # "attend" without a date or deliverable
    assert not passes_forgettability("attend the meeting")


# ---------- _task_description_for_forgettability ----------

def test_task_description_strips_owner_and_metadata():
    line = "- [ ] [[Sam-Rivera]] read PRD [created:: 2026-05-13] [source:: [[note]]]"
    assert _task_description_for_forgettability(line) == "read PRD"

def test_task_description_strips_due_field():
    line = "- [ ] [[Sam-Rivera]] send deck [due:: 2026-05-20] [source:: [[n]]]"
    assert _task_description_for_forgettability(line) == "send deck"

def test_task_description_non_task_returns_empty():
    assert _task_description_for_forgettability("just a regular bullet") == ""


# ---------- _demote_with_marker ----------

def test_demote_strips_checkbox_and_adds_marker():
    line = "- [ ] [[Sam-Rivera]] read PRD [created:: 2026-05-13]"
    result = _demote_with_marker(line, "forgettability")
    assert "[ ]" not in result
    assert "[demoted:: forgettability]" in result
    assert "[[Sam-Rivera]]" in result
    assert "read PRD" in result
    assert "[created:: 2026-05-13]" in result

def test_demote_preserves_trailing_newline():
    line = "- [ ] [[Sam-Rivera]] read PRD\n"
    result = _demote_with_marker(line, "forgettability")
    assert result.endswith("\n")

def test_demote_no_double_marker():
    line = "- [[Sam-Rivera]] x [demoted:: forgettability]"
    # Already demoted, should be no-op-ish (does not duplicate marker)
    result = _demote_with_marker(line, "forgettability")
    assert result.count("[demoted::") == 1

def test_demote_non_task_passes_through():
    line = "regular text without checkbox"
    assert _demote_with_marker(line, "forgettability") == line


# ---------- apply_task_hygiene + forgettability ----------

def test_hygiene_owner_task_no_signal_demotes():
    line = "- [ ] [[Sam-Rivera]] read PRD [source:: [[note]]]"
    fm = {"type": "meeting", "date": "2026-05-13", "attendees": ["[[Sam-Rivera]]", "[[Mia-Fischer]]"]}
    result = apply_task_hygiene(line, fm, vip_slugs=VIP_SLUGS)
    assert "[ ]" not in result
    assert "[demoted:: forgettability]" in result
    assert "[created:: 2026-05-13]" in result

def test_hygiene_owner_task_with_signal_keeps():
    line = "- [ ] [[Sam-Rivera]] read the consultant deck before Thursday [source:: [[note]]]"
    fm = {"type": "meeting", "date": "2026-05-13", "attendees": ["[[Sam-Rivera]]", "[[Mia-Fischer]]"]}
    result = apply_task_hygiene(line, fm, vip_slugs=VIP_SLUGS)
    assert "- [ ]" in result
    assert "[demoted::" not in result
    assert "[created:: 2026-05-13]" in result

def test_hygiene_owner_task_delegated_bypasses_forgettability():
    # If Sam has [delegated-by:: someone], it's an explicit ask from someone, keep
    line = "- [ ] [[Sam-Rivera]] read PRD [delegated-by:: [[Jordan-Lee]]] [source:: [[n]]]"
    fm = {"type": "meeting", "date": "2026-05-13", "attendees": ["[[Sam-Rivera]]", "[[Jordan-Lee]]"]}
    result = apply_task_hygiene(line, fm, vip_slugs=VIP_SLUGS)
    assert "- [ ]" in result
    assert "[demoted::" not in result

def test_hygiene_non_owner_task_no_forgettability_applied():
    # Forgettability ONLY applies to Sam-owned tasks. Mia committing to read X stays.
    line = "- [ ] [[Mia-Fischer]] read PRD [source:: [[n]]]"
    fm = {"type": "meeting", "date": "2026-05-13", "attendees": ["[[Sam-Rivera]]", "[[Mia-Fischer]]"]}
    result = apply_task_hygiene(line, fm, vip_slugs=VIP_SLUGS)
    # Existing 1on1/small-meeting path auto-adds [delegated-by:: [[Sam-Rivera]]]
    assert "- [ ]" in result
    assert "[delegated-by:: [[Sam-Rivera]]]" in result
    assert "[demoted::" not in result

def test_hygiene_vip_owner_task_no_signal_still_demotes():
    # VIP protection guards OTHER people's tasks. Sam's own tasks still get filtered
    # even when a VIP is in the room, Sam owning "read PRD" in a meeting with Jordan
    # is still a conversational artifact.
    line = "- [ ] [[Sam-Rivera]] read PRD [source:: [[n]]]"
    fm = {"type": "meeting", "date": "2026-05-13",
          "attendees": ["[[Sam-Rivera]]", "[[Jordan-Lee]]"],
          "vip-involved": ["boss-chain"]}
    result = apply_task_hygiene(line, fm, vip_slugs=VIP_SLUGS)
    assert "[demoted:: forgettability]" in result

def test_hygiene_completed_owner_task_no_demote():
    # Done tasks aren't transformed (existing behavior preserved)
    line = "- [x] [[Sam-Rivera]] read PRD"
    fm = {"type": "meeting", "date": "2026-05-13", "attendees": ["[[Sam-Rivera]]", "[[Mia-Fischer]]"]}
    result = apply_task_hygiene(line, fm, vip_slugs=VIP_SLUGS)
    assert "- [x]" in result
    assert "[demoted::" not in result
