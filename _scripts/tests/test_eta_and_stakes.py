"""Tests for the /w-daily ETA heads-up and stakes triage in classify-inbox.py.

Stakes decides which transcripts run on Haiku (low-stakes knowledge transfer)
vs Sonnet (substantive), and the ETA feeds the one-line heads-up the skill
prints on a heavy morning, so both need to stay honest.
"""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load(mod_name, filename):
    spec = importlib.util.spec_from_file_location(
        mod_name, Path(__file__).resolve().parents[1] / filename
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ci = _load("classify_inbox", "classify-inbox.py")


# --- classify_transcript_stakes ------------------------------------------
def test_stakes_1on1_is_substantive_even_with_learning_subject():
    assert ci.classify_transcript_stakes({"meeting_type": "1on1", "subject": "training plan"}) == "substantive"


def test_stakes_steerco_is_substantive():
    assert ci.classify_transcript_stakes({"meeting_type": "steerco", "subject": "lecture series"}) == "substantive"


def test_stakes_training_subject_is_low():
    assert ci.classify_transcript_stakes({"meeting_type": "general", "subject": "Training: AWS basics"}) == "low-stakes"


def test_stakes_normal_meeting_is_substantive():
    assert ci.classify_transcript_stakes({"meeting_type": "general", "subject": "Project sync"}) == "substantive"


def test_stakes_conservative_detection_table():
    # Normalizer strips leading date + generic "Meeting:"/"Sync:" prefixes; a
    # marker must START the title or be its TRAILING noun. A mid-subject marker
    # word must NOT defer a substantive meeting.
    cases = [
        ("general", "Demo: tool walkthrough", "low-stakes"),
        ("general", "06-30 Personal Productivity and Knowledge Management System Demonstration", "low-stakes"),
        ("general", "06-30 Meeting: Demo of the new dashboard", "low-stakes"),
        ("general", "Onboarding: new joiner setup", "low-stakes"),
        ("general", "Q2 OKR Review and Q3 Planning", "substantive"),
        ("general", "Q3 training budget review", "substantive"),   # mid-subject marker, no over-match
        ("general", "06-30 Meeting: RFP Data Consolidation", "substantive"),
        ("1on1", "Demo Prep", "substantive"),                      # meeting-type guard wins
    ]
    for mt, subj, expected in cases:
        got = ci.classify_transcript_stakes({"meeting_type": mt, "subject": subj})
        assert got == expected, f"{subj!r} -> {got}, expected {expected}"


# --- _duration_to_seconds ------------------------------------------------
def test_duration_hms():
    assert ci._duration_to_seconds("1:30:00") == 5400


def test_duration_ms():
    assert ci._duration_to_seconds("45:00") == 2700


def test_duration_bad_input_is_zero():
    assert ci._duration_to_seconds("") == 0
    assert ci._duration_to_seconds("nope") == 0


# --- estimate_transcript_minutes -----------------------------------------
def test_minutes_low_stakes():
    assert ci.estimate_transcript_minutes({}, "low-stakes") == ci.ETA_TRANSCRIPT_LOW


def test_minutes_long_recording_is_long():
    assert ci.estimate_transcript_minutes({"recording_duration": "1:00:00"}, "substantive") == ci.ETA_TRANSCRIPT_LONG


def test_minutes_short_substantive():
    assert ci.estimate_transcript_minutes({"recording_duration": "20:00"}, "substantive") == ci.ETA_TRANSCRIPT_SUBSTANTIVE


# --- estimate_eta --------------------------------------------------------
def test_eta_empty_is_zero_and_not_slow():
    eta = ci.estimate_eta({}, {})
    assert eta["full_minutes"] == 0
    assert eta["transcript_count"] == 0
    assert eta["slow"] is False


def test_eta_stamps_transcripts_and_flags_slow():
    result = {
        "email_manifest": [{"file": "a"}],
        "threads": [{"emails": [1]}],
        "batches": [],
        "transcripts": [
            {"meeting_type": "1on1", "subject": "Jordan sync", "recording_duration": "20:00"},
            {"meeting_type": "general", "subject": "Training: AWS", "recording_duration": "50:00"},
        ],
    }
    eta = ci.estimate_eta(result, {"docs": 2})
    # Each transcript is stamped in place with its stakes class + per-item estimate.
    assert result["transcripts"][0]["stakes"] == "substantive"
    assert result["transcripts"][1]["stakes"] == "low-stakes"
    assert all("est_minutes" in t for t in result["transcripts"])
    assert eta["transcript_count"] == 2
    # Two transcripts push the run over the heads-up threshold.
    assert eta["slow"] is True


def test_eta_parallel_model_slowest_plus_contention():
    # All transcripts dispatch in one block: wall-clock is the slowest plus the
    # contention share of the rest, never the sum.
    result = {"transcripts": [
        {"meeting_type": "general", "subject": f"Meeting {i}", "recording_duration": "1:00:00"}
        for i in range(4)
    ]}
    eta = ci.estimate_eta(result, {})
    per_item = ci.ETA_TRANSCRIPT_LONG
    expected = per_item + ci.ETA_CONTENTION_FACTOR * per_item * 3
    assert eta["full_minutes"] == int(round(expected))
    assert eta["full_minutes"] < int(per_item * 4)
