"""Tests for finalize.py, the v2 pipeline finalizer.

Ports the path-escape guard tests from test_write_notes_path_guard.py (adapted to
the staged-notes flow: agents Write complete .md notes into _db/staged-notes/ and
finalize validates + moves them) and adds the staged happy path and
quarantine-on-invalid coverage the finalize spec calls for.
"""
import importlib.util
import json
import subprocess
import sys
import unicodedata
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "finalize.py"

# Import finalize as a module for the unit-level guard tests.
_spec = importlib.util.spec_from_file_location("finalize", SCRIPT)
finalize = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(SCRIPT.parent))
_spec.loader.exec_module(finalize)


EMAIL_NOTE = """---
date: 2026-07-03
type: email
interaction-type: email
from: "[[Sam-Rivera]]"
to:
  - "[[Mia-Fischer]]"
subject: Test subject
summary: A short plain summary of the thread
conversation-id: CONV123
direction: sent
relevance: high
source-file: {source}
---

Body text.
"""


MEETING_NOTE = """---
date: 2026-07-06
type: meeting
interaction-type: meeting
meeting-type: {mtype}
summary: A meeting summary line
attendees:
  - "[[Recorder-import]]"
source-file: {source}
---

Discussed things.
"""


def _scaffold(vault: Path):
    """Create the minimal vault skeleton finalize expects."""
    for sub in ("_db/staged-notes", "00-Inbox/_processing", "05-Interactions",
                "08-Reference", "_attachments"):
        (vault / sub).mkdir(parents=True, exist_ok=True)
    (vault / "_db" / "thread-index.json").write_text(
        '{"by_conversation_id":{},"by_normalized_subject":{}}', encoding="utf-8")
    (vault / "_db" / "ingest-log.json").write_text("[]", encoding="utf-8")
    (vault / "_db" / "entity-registry.json").write_text('{"people":[]}', encoding="utf-8")


def _write_registry(vault: Path, people: list):
    (vault / "_db" / "entity-registry.json").write_text(
        json.dumps({"people": people}), encoding="utf-8")


def _write_manifest(vault: Path, **sections):
    manifest = {"email_manifest": [], "transcripts": [], "docs": [],
                "definitive_lows": []}
    manifest.update(sections)
    (vault / "_db" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _run(vault: Path, skips=None):
    cmd = [sys.executable, str(SCRIPT), "--vault", str(vault)]
    if skips is not None:
        skips_path = vault / "skips.json"
        skips_path.write_text(json.dumps(skips), encoding="utf-8")
        cmd += ["--skips", str(skips_path)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _ingest_log(vault: Path):
    return json.loads((vault / "_db" / "ingest-log.json").read_text(encoding="utf-8"))


# ---------- assert_within_vault (unit) ----------

def test_assert_within_vault_rejects_traversal(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        finalize.assert_within_vault(tmp_path / ".." / "escape.md", tmp_path)


def test_assert_within_vault_allows_in_vault(tmp_path):
    ok = finalize.assert_within_vault(tmp_path / "05-Interactions" / "x.md", tmp_path)
    assert str(ok).startswith(str(tmp_path.resolve()))


# ---------- source-path escape (ported: must not touch files outside vault) ----------

def test_source_delete_traversal_rejected(tmp_path):
    """A `../` source in the manifest must not delete a file outside the vault.
    The note still gets written; only the escaping source cleanup is refused."""
    _scaffold(tmp_path)
    victim = tmp_path.parent / "finalize-victim.txt"
    victim.write_text("do not delete", encoding="utf-8")
    try:
        _write_manifest(tmp_path, email_manifest=[
            {"file": "../finalize-victim.txt", "output_filename": "2026-07-03-email-test.md"}])
        (tmp_path / "_db" / "staged-notes" / "2026-07-03-email-test.md").write_text(
            EMAIL_NOTE.format(source="finalize-victim.txt"), encoding="utf-8")

        result = _run(tmp_path)

        assert victim.exists(), "guard must not delete a file outside the vault"
        assert victim.read_text(encoding="utf-8") == "do not delete"
        assert any("escape" in e.lower() for e in result["errors"])
        # The note itself is still written (only the source cleanup was blocked).
        assert "05-Interactions/2026/2026-07-03-email-test.md" in result["written"]
    finally:
        victim.unlink(missing_ok=True)


# ---------- staged happy path ----------

def test_staged_email_happy_path(tmp_path):
    _scaffold(tmp_path)
    src = tmp_path / "00-Inbox" / "_processing" / "SENT-email.txt"
    src.write_text("raw email", encoding="utf-8")
    _write_manifest(tmp_path, email_manifest=[
        {"file": "00-Inbox/_processing/SENT-email.txt",
         "output_filename": "2026-07-03-email-test.md"}])
    (tmp_path / "_db" / "staged-notes" / "2026-07-03-email-test.md").write_text(
        EMAIL_NOTE.format(source="SENT-email.txt"), encoding="utf-8")

    result = _run(tmp_path)

    out = tmp_path / "05-Interactions" / "2026" / "2026-07-03-email-test.md"
    assert out.exists()
    assert "05-Interactions/2026/2026-07-03-email-test.md" in result["written"]
    assert not src.exists(), "email source should be deleted after write"
    assert result["touched_dates"] == ["2026-07-03"]
    assert result["errors"] == []
    # ingest-log gets a 'created' entry; thread index picks up the note.
    log = _ingest_log(tmp_path)
    assert any(e["action"] == "created" and e["type"] == "email" for e in log)
    idx = json.loads((tmp_path / "_db" / "thread-index.json").read_text(encoding="utf-8"))
    assert "CONV123" in idx["by_conversation_id"]


# ---------- quarantine on invalid frontmatter ----------

def test_quarantine_on_invalid_frontmatter(tmp_path):
    """A staged note missing a required field is quarantined, the source is left
    in _processing for reprocessing, and a 'failed' entry is logged."""
    _scaffold(tmp_path)
    src = tmp_path / "00-Inbox" / "_processing" / "SENT-email.txt"
    src.write_text("raw email", encoding="utf-8")
    _write_manifest(tmp_path, email_manifest=[
        {"file": "00-Inbox/_processing/SENT-email.txt",
         "output_filename": "2026-07-03-email-test.md"}])
    # Drop the required `summary` field.
    bad = EMAIL_NOTE.format(source="SENT-email.txt").replace(
        "summary: A short plain summary of the thread\n", "")
    (tmp_path / "_db" / "staged-notes" / "2026-07-03-email-test.md").write_text(
        bad, encoding="utf-8")

    result = _run(tmp_path)

    assert result["written"] == []
    assert any("2026-07-03-email-test.md" in q for q in result["quarantined"])
    assert (tmp_path / "_db" / "staged-notes" / "_quarantine"
            / "2026-07-03-email-test.md").exists()
    assert src.exists(), "source must stay in _processing on validation failure"
    log = _ingest_log(tmp_path)
    assert any(e["action"] == "failed" for e in log)


def test_unknown_staged_file_quarantined(tmp_path):
    _scaffold(tmp_path)
    _write_manifest(tmp_path)  # empty manifest, nothing expected
    (tmp_path / "_db" / "staged-notes" / "orphan.md").write_text(
        EMAIL_NOTE.format(source="x.txt"), encoding="utf-8")

    result = _run(tmp_path)

    assert result["written"] == []
    assert any("orphan.md" in q for q in result["quarantined"])
    assert any("unknown" in w.lower() for w in result["warnings"])


def test_target_exists_quarantines_not_overwrites(tmp_path):
    """belt-and-suspenders: if the final path already exists, quarantine the
    staged note rather than overwrite the existing one."""
    _scaffold(tmp_path)
    (tmp_path / "05-Interactions" / "2026").mkdir(parents=True)
    existing = tmp_path / "05-Interactions" / "2026" / "2026-07-03-email-test.md"
    existing.write_text("ORIGINAL CONTENT", encoding="utf-8")
    src = tmp_path / "00-Inbox" / "_processing" / "SENT-email.txt"
    src.write_text("raw", encoding="utf-8")
    _write_manifest(tmp_path, email_manifest=[
        {"file": "00-Inbox/_processing/SENT-email.txt",
         "output_filename": "2026-07-03-email-test.md"}])
    (tmp_path / "_db" / "staged-notes" / "2026-07-03-email-test.md").write_text(
        EMAIL_NOTE.format(source="SENT-email.txt"), encoding="utf-8")

    result = _run(tmp_path)

    assert existing.read_text(encoding="utf-8") == "ORIGINAL CONTENT"
    assert result["written"] == []
    assert any("2026-07-03-email-test.md" in q for q in result["quarantined"])
    assert any("exist" in e.lower() for e in result["errors"])


# ---------- unicode-robust source deletion ----------

def test_unicode_nfc_nfd_source_deleted(tmp_path):
    """A source stored under a different unicode normal form than the manifest
    string must still be deleted (the NFC/NFD survival bug)."""
    nfc_name = unicodedata.normalize("NFC", "SENT-café-résumé.txt")
    nfd_name = unicodedata.normalize("NFD", nfc_name)
    if nfc_name == nfd_name:
        import pytest
        pytest.skip("no NFC/NFD difference for this name on this platform")

    _scaffold(tmp_path)
    # File on disk uses the NFD byte sequence; manifest references NFC.
    (tmp_path / "00-Inbox" / "_processing" / nfd_name).write_text("raw", encoding="utf-8")
    _write_manifest(tmp_path, email_manifest=[
        {"file": f"00-Inbox/_processing/{nfc_name}",
         "output_filename": "2026-07-03-email-test.md"}])
    (tmp_path / "_db" / "staged-notes" / "2026-07-03-email-test.md").write_text(
        EMAIL_NOTE.format(source=nfc_name), encoding="utf-8")

    result = _run(tmp_path)

    assert "05-Interactions/2026/2026-07-03-email-test.md" in result["written"]
    remaining = list((tmp_path / "00-Inbox" / "_processing").iterdir())
    assert remaining == [], f"unicode source not deleted: {remaining}"


# ---------- --skips ----------

# ---------- meeting-type correction renames the destination ----------

def _stage_meeting(vault, assigned, mtype, src_name="transcript-x.txt"):
    (vault / "00-Inbox" / "_processing" / src_name).write_text("raw", encoding="utf-8")
    _write_manifest(vault, transcripts=[
        {"file": f"00-Inbox/_processing/{src_name}", "output_filename": assigned}])
    (vault / "_db" / "staged-notes" / assigned).write_text(
        MEETING_NOTE.format(mtype=mtype, source=src_name), encoding="utf-8")


def test_meeting_type_correction_renames(tmp_path):
    _scaffold(tmp_path)
    _stage_meeting(tmp_path, "2026-07-06-1on1-halftime-review.md", "general")

    result = _run(tmp_path)

    corrected = tmp_path / "05-Interactions" / "2026" / "2026-07-06-general-halftime-review.md"
    assert corrected.exists()
    assert not (tmp_path / "05-Interactions" / "2026" / "2026-07-06-1on1-halftime-review.md").exists()
    assert "05-Interactions/2026/2026-07-06-general-halftime-review.md" in result["written"]
    assert result["renamed"] == [{"from": "2026-07-06-1on1-halftime-review.md",
                                  "to": "2026-07-06-general-halftime-review.md"}]
    # ingest-log records the renamed path.
    log = _ingest_log(tmp_path)
    created = [e for e in log if e["action"] == "created"]
    assert created[0]["output-file"] == "05-Interactions/2026/2026-07-06-general-halftime-review.md"


def test_meeting_type_correction_collision_appends(tmp_path):
    """The corrected name skipped classify pre-resolution, so a collision on it
    gets -2 rather than quarantine."""
    _scaffold(tmp_path)
    (tmp_path / "05-Interactions" / "2026").mkdir(parents=True)
    (tmp_path / "05-Interactions" / "2026" / "2026-07-06-general-halftime-review.md").write_text(
        "PRE-EXISTING", encoding="utf-8")
    _stage_meeting(tmp_path, "2026-07-06-1on1-halftime-review.md", "general")

    result = _run(tmp_path)

    assert "05-Interactions/2026/2026-07-06-general-halftime-review-2.md" in result["written"]
    assert result["renamed"][0]["to"] == "2026-07-06-general-halftime-review-2.md"
    assert (tmp_path / "05-Interactions" / "2026"
            / "2026-07-06-general-halftime-review.md").read_text(encoding="utf-8") == "PRE-EXISTING"


def test_meeting_type_matching_filename_not_renamed(tmp_path):
    _scaffold(tmp_path)
    _stage_meeting(tmp_path, "2026-07-06-1on1-halftime-review.md", "1on1")

    result = _run(tmp_path)

    assert "05-Interactions/2026/2026-07-06-1on1-halftime-review.md" in result["written"]
    assert result["renamed"] == []


# ---------- manifest-driven skip lists ----------

def test_pre_skipped_deleted_and_logged(tmp_path):
    _scaffold(tmp_path)
    src = tmp_path / "00-Inbox" / "_processing" / "dup-email.txt"
    src.write_text("raw", encoding="utf-8")
    _write_manifest(tmp_path, pre_skipped=[
        {"file": "00-Inbox/_processing/dup-email.txt", "filename": "dup-email.txt",
         "reason": "already processed", "subject": "Re: thing", "date": "2026-07-02"}])

    result = _run(tmp_path)

    assert not src.exists()
    assert "dup-email.txt" in result["skipped"]
    log = _ingest_log(tmp_path)
    assert any(e["action"] == "skipped-duplicate" and e["source-file"] == "dup-email.txt"
               for e in log)


def test_skipped_transcript_moved_to_attachments(tmp_path):
    _scaffold(tmp_path)
    proc = tmp_path / "00-Inbox" / "_processing"
    (proc / "transcript-dup.txt").write_text("raw", encoding="utf-8")
    (proc / "transcript-dup.json").write_text("{}", encoding="utf-8")
    _write_manifest(tmp_path, skipped_transcripts=[
        {"file": "00-Inbox/_processing/transcript-dup.txt", "subject": "Dup rec",
         "reason": "recovered-duplicate"}])

    result = _run(tmp_path)

    assert not (proc / "transcript-dup.txt").exists()
    assert (tmp_path / "_attachments" / "transcript-dup.txt").exists()
    assert (tmp_path / "_attachments" / "transcript-dup.json").exists(), "companion kept too"
    assert "transcript-dup.txt" in result["skipped"]
    log = _ingest_log(tmp_path)
    assert any(e["action"] == "skipped-duplicate" and e["type"] == "meeting" for e in log)


def test_agent_inputs_purged_after_run(tmp_path):
    _scaffold(tmp_path)
    agent_inputs = tmp_path / "_db" / "agent-inputs"
    agent_inputs.mkdir()
    (agent_inputs / "transcript-x.txt").write_text("sanitized copy", encoding="utf-8")
    _write_manifest(tmp_path)

    _run(tmp_path)

    assert list(agent_inputs.iterdir()) == [], "stale agent-input copies should be purged"


# ---------- defensive summary quoting (unparseable-frontmatter rescue) ----------

def test_unquoted_summary_rescued(tmp_path):
    """A bare summary containing ': ' breaks YAML; finalize quotes it, retries,
    writes the note, and records a warning instead of quarantining."""
    _scaffold(tmp_path)
    src = tmp_path / "00-Inbox" / "_processing" / "SENT-email.txt"
    src.write_text("raw", encoding="utf-8")
    _write_manifest(tmp_path, email_manifest=[
        {"file": "00-Inbox/_processing/SENT-email.txt",
         "output_filename": "2026-07-03-email-test.md"}])
    note = EMAIL_NOTE.format(source="SENT-email.txt").replace(
        "summary: A short plain summary of the thread",
        "summary: All-hands: World Cup wrap, AI shift")
    (tmp_path / "_db" / "staged-notes" / "2026-07-03-email-test.md").write_text(
        note, encoding="utf-8")

    result = _run(tmp_path)

    out = tmp_path / "05-Interactions" / "2026" / "2026-07-03-email-test.md"
    assert "05-Interactions/2026/2026-07-03-email-test.md" in result["written"]
    assert result["quarantined"] == []
    assert any("auto-quoted summary" in w for w in result["warnings"])
    # The written note carries the now-quoted summary and re-parses cleanly.
    written = out.read_text(encoding="utf-8")
    assert 'summary: "All-hands: World Cup wrap, AI shift"' in written


# ---------- deterministic VIP stamping from the registry ----------

def test_vip_stamped_on_meeting(tmp_path):
    _scaffold(tmp_path)
    _write_registry(tmp_path, [
        {"name": "Morgan Hayes", "vip": "boss-chain"},
        {"name": "Mia Fischer", "vip": "team"}])
    src = tmp_path / "00-Inbox" / "_processing" / "transcript-x.txt"
    src.write_text("raw", encoding="utf-8")
    _write_manifest(tmp_path, transcripts=[
        {"file": "00-Inbox/_processing/transcript-x.txt",
         "output_filename": "2026-07-06-1on1-town-hall.md"}])
    note = MEETING_NOTE.format(mtype="1on1", source="transcript-x.txt").replace(
        '  - "[[Recorder-import]]"', '  - "[[Morgan-Hayes]]"\n  - "[[Mia-Fischer]]"')
    (tmp_path / "_db" / "staged-notes" / "2026-07-06-1on1-town-hall.md").write_text(
        note, encoding="utf-8")

    result = _run(tmp_path)

    out = tmp_path / "05-Interactions" / "2026" / "2026-07-06-1on1-town-hall.md"
    written = out.read_text(encoding="utf-8")
    assert "vip-involved:" in written
    assert "- boss-chain" in written
    assert "- team" in written
    assert "- vip/boss-chain" in written
    assert "05-Interactions/2026/2026-07-06-1on1-town-hall.md" in result["vip_stamped"]


def test_vip_stamp_noop_when_already_present(tmp_path):
    """If the note already carries vip-involved (manifest pre-computed it), leave
    it untouched: no second vip-involved, not reported as stamped."""
    _scaffold(tmp_path)
    _write_registry(tmp_path, [{"name": "Morgan Hayes", "vip": "boss-chain"}])
    src = tmp_path / "00-Inbox" / "_processing" / "transcript-x.txt"
    src.write_text("raw", encoding="utf-8")
    _write_manifest(tmp_path, transcripts=[
        {"file": "00-Inbox/_processing/transcript-x.txt",
         "output_filename": "2026-07-06-1on1-town-hall.md"}])
    note = MEETING_NOTE.format(mtype="1on1", source="transcript-x.txt").replace(
        '  - "[[Recorder-import]]"',
        '  - "[[Morgan-Hayes]]"\nvip-involved:\n  - stakeholder')
    (tmp_path / "_db" / "staged-notes" / "2026-07-06-1on1-town-hall.md").write_text(
        note, encoding="utf-8")

    result = _run(tmp_path)

    out = tmp_path / "05-Interactions" / "2026" / "2026-07-06-1on1-town-hall.md"
    written = out.read_text(encoding="utf-8")
    assert written.count("vip-involved:") == 1
    assert "- stakeholder" in written           # original preserved
    assert "- boss-chain" not in written        # not re-derived from registry
    assert result["vip_stamped"] == []


def test_skips_delete_and_move(tmp_path):
    _scaffold(tmp_path)
    _write_manifest(tmp_path)
    proc = tmp_path / "00-Inbox" / "_processing"
    (proc / "dup.txt").write_text("dup", encoding="utf-8")
    (proc / "keep.txt").write_text("keep", encoding="utf-8")

    result = _run(tmp_path, skips=[
        {"source_file": "dup.txt", "reason": "duplicate send"},
        {"source_file": "keep.txt", "reason": "verbatim kept", "move_to_attachments": True},
    ])

    assert not (proc / "dup.txt").exists(), "delete-skip source should be removed"
    assert not (proc / "keep.txt").exists(), "move-skip source should leave _processing"
    assert (tmp_path / "_attachments" / "keep.txt").exists()
    assert set(result["skipped"]) == {"dup.txt", "keep.txt"}
    log = _ingest_log(tmp_path)
    assert any(e["action"] == "skipped-duplicate" for e in log)


# --- move_email_attachments -------------------------------------------------

def _stage_attachment(vault, name):
    d = vault / "00-Inbox" / "_email-attachments"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("payload", encoding="utf-8")
    return d


def _blank_result():
    return {"moved_to_attachments": [], "warnings": []}


def test_move_email_attachments_lands_under_stamp(tmp_path):
    name = "2026-01-09_001240-01-deck.pdf"
    staging = _stage_attachment(tmp_path, name)
    result = _blank_result()

    finalize.move_email_attachments([name], "2026-01-09_001240", tmp_path, result)

    dest = tmp_path / "_attachments" / "email" / "2026-01-09_001240" / name
    assert dest.exists(), "attachment should land in the stamp folder"
    assert not (staging / name).exists(), "staged copy should be moved, not copied"
    assert result["moved_to_attachments"] == [
        f"_attachments/email/2026-01-09_001240/{name}"]


def test_move_email_attachments_warns_on_missing_source(tmp_path):
    """A manifest naming a file that is gone must warn, not crash the run."""
    result = _blank_result()
    finalize.move_email_attachments(["gone.pdf"], "2026-01-09_001240", tmp_path, result)
    assert result["moved_to_attachments"] == []
    assert any("gone.pdf" in w for w in result["warnings"])


def test_move_email_attachments_does_not_rename_on_reencounter(tmp_path):
    """Same stamp + same name is the same file (a re-run), not a collision.

    Renaming to -2 would strand the frontmatter wikilink classify-inbox already
    wrote, which points at the original name.
    """
    name = "2026-01-09_001240-01-deck.pdf"
    existing = tmp_path / "_attachments" / "email" / "2026-01-09_001240"
    existing.mkdir(parents=True)
    (existing / name).write_text("original", encoding="utf-8")
    _stage_attachment(tmp_path, name)
    result = _blank_result()

    finalize.move_email_attachments([name], "2026-01-09_001240", tmp_path, result)

    assert (existing / name).read_text(encoding="utf-8") == "original"
    assert not (existing / "2026-01-09_001240-01-deck-2.pdf").exists()
    assert any("already present" in w for w in result["warnings"])


def test_move_email_attachments_noop_without_stamp(tmp_path):
    name = "loose.pdf"
    _stage_attachment(tmp_path, name)
    result = _blank_result()
    finalize.move_email_attachments([name], "", tmp_path, result)
    assert result["moved_to_attachments"] == []
    assert not (tmp_path / "_attachments" / "email").exists()


# --- promoted email attachments (docs with is_email_attachment) -------------

REF_NOTE = """---
date: 2026-07-03
type: reference
source-file: {source}
summary: "A promoted deck summary"
source-email: "[[2026-07-03-email-test-subject]]"
---

Deck content.
"""


def test_promoted_email_attachment_is_moved_not_deleted(tmp_path):
    """A docs entry flagged is_email_attachment must produce its reference note
    AND leave the raw file for the parent email's move_email_attachments, which
    relocates it to _attachments/email/<stamp>/. Deleting it as a doc source
    would strand the email note's attachment wikilink."""
    _scaffold(tmp_path)
    stamp = "2026-07-03_101500"
    att_name = f"{stamp}-01-Q3 deck.pptx"

    att_dir = tmp_path / "00-Inbox" / "_email-attachments"
    att_dir.mkdir(parents=True, exist_ok=True)
    (att_dir / att_name).write_text("rawbytes", encoding="utf-8")

    email_src = tmp_path / "00-Inbox" / "_processing" / "email.txt"
    email_src.write_text("x", encoding="utf-8")

    email_ofn = "2026-07-03-email-test-subject.md"
    ref_ofn = "2026-07-03-Q3 deck.md"
    (tmp_path / "_db" / "staged-notes" / email_ofn).write_text(
        EMAIL_NOTE.format(source="email.txt"), encoding="utf-8")
    (tmp_path / "_db" / "staged-notes" / ref_ofn).write_text(
        REF_NOTE.format(source=att_name), encoding="utf-8")

    _write_manifest(
        tmp_path,
        email_manifest=[{
            "file": "00-Inbox/_processing/email.txt",
            "output_filename": email_ofn,
            "attachments": [att_name],
            "attachment_stamp": stamp,
        }],
        docs=[{
            "file": f"00-Inbox/_email-attachments/{att_name}",
            "filename": att_name,
            "output_filename": ref_ofn,
            "is_email_attachment": True,
            "source_email": "2026-07-03-email-test-subject",
        }],
    )
    res = _run(tmp_path)

    # Both the email interaction note and the promoted reference note land.
    assert any(email_ofn in w for w in res["written"]), res
    assert any(ref_ofn in w for w in res["written"]), res
    # The raw attachment is relocated (linked), never deleted or stranded.
    moved = tmp_path / "_attachments" / "email" / stamp / att_name
    assert moved.exists(), res
    assert not (att_dir / att_name).exists(), res
    assert all(att_name not in d for d in res["deleted_sources"]), res
    assert any(att_name in m for m in res["moved_to_attachments"]), res
