"""Tests for email-attachment correlation in classify-inbox.py.

The capture flow saves attachments as `<yyyy-MM-dd_HHmmss>-NN-<original name>`,
stamped from the same receivedDateTime that lands in the email's `Date:` header.
Nothing else ties the two together: there is no Attachments: header, and the
filename carries no thread or message id. So the receive-second IS the join key,
and these tests pin the two failure modes that would silently break it.

1. Stamp drift. The flow's formatDateTime does NOT shift the UTC offset
   (verified against live output: an input of `2026-01-09T00:12:40+00:00`
   produced `2026-01-09_001240`). Parsing the header to an aware datetime and
   formatting it back would "helpfully" convert to local time and match nothing.

2. Glob metacharacters. Outlook suffixes duplicate attachment names with `[1]`.
   glob() reads that as a character class and silently returns no match, so
   matching goes through iterdir() + startswith instead.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib.util
spec = importlib.util.spec_from_file_location(
    "classify_inbox",
    Path(__file__).resolve().parents[1] / "classify-inbox.py",
)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


# --- attachment_stamp -------------------------------------------------------

def test_stamp_matches_live_flow_output():
    """The exact header/filename pair observed from a real flow run."""
    assert ci.attachment_stamp("2026-01-09T00:12:40+00:00") == "2026-01-09_001240"


def test_stamp_does_not_shift_timezone():
    """A non-zero offset must NOT be normalised to UTC.

    The flow formats the string as-is, so converting here would invent a
    mismatch against a filename that was never shifted.
    """
    assert ci.attachment_stamp("2026-01-09T00:12:40+02:00") == "2026-01-09_001240"


def test_stamp_rejects_malformed_dates():
    for bad in ("", "2026-01-09", "garbage", "2026-01-09 00:12:40",
                "20260109T001240Z", "not-a-date-at-all!!"):
        assert ci.attachment_stamp(bad) == "", bad


# --- find_staged_attachments ------------------------------------------------

def _stage(tmp_path, names):
    d = tmp_path / "_email-attachments"
    d.mkdir()
    for n in names:
        (d / n).write_text("x")
    return d


def test_matches_only_same_receive_second(tmp_path):
    """An adjacent second is a different email and must not be swept in."""
    d = _stage(tmp_path, [
        "2026-01-09_001240-01-deck.pdf",
        "2026-01-09_001240-02-sheet.xlsx",
        "2026-01-09_001241-01-other-email.pdf",
    ])
    assert ci.find_staged_attachments("2026-01-09_001240", d) == [
        "2026-01-09_001240-01-deck.pdf",
        "2026-01-09_001240-02-sheet.xlsx",
    ]


def test_same_filename_on_two_emails_does_not_collide(tmp_path):
    """The case that motivated the whole change: one thread, two senders, one
    filename. Different receive-seconds must keep them apart."""
    d = _stage(tmp_path, [
        "2026-01-09_001240-01-deck.pdf",
        "2026-01-10_143005-01-deck.pdf",
    ])
    assert ci.find_staged_attachments("2026-01-09_001240", d) == [
        "2026-01-09_001240-01-deck.pdf"]
    assert ci.find_staged_attachments("2026-01-10_143005", d) == [
        "2026-01-10_143005-01-deck.pdf"]


def test_bracketed_name_is_found(tmp_path):
    """Outlook's `[1]` duplicate suffix is a glob character class. glob() would
    return nothing here."""
    name = "2026-01-09_001240-01-Quarterly Report[1].pdf"
    d = _stage(tmp_path, [name])
    assert ci.find_staged_attachments("2026-01-09_001240", d) == [name]


def test_empty_stamp_matches_nothing(tmp_path):
    """A malformed Date: header yields '' and must not match every file."""
    d = _stage(tmp_path, ["2026-01-09_001240-01-deck.pdf"])
    assert ci.find_staged_attachments("", d) == []


def test_missing_staging_dir_is_not_an_error(tmp_path):
    assert ci.find_staged_attachments("2026-01-09_001240", tmp_path / "absent") == []


# --- frontmatter ------------------------------------------------------------

def _fm(stamp, names):
    return ci.generate_email_frontmatter({
        "filename": "src.txt", "subject": "S", "date": "2026-01-09",
        "attachment_stamp": stamp, "attachments": names,
        "resolved_from": {}, "resolved_to": [], "resolved_cc": [],
    })


def test_frontmatter_links_attachments():
    fm = _fm("2026-01-09_001240", ["2026-01-09_001240-01-deck.pdf"])
    assert fm["attachments"] == [
        "[[email/2026-01-09_001240/2026-01-09_001240-01-deck.pdf]]"]


def test_frontmatter_omits_key_when_no_attachments():
    """An absent key, not an empty list: matches how cc/direction behave."""
    assert "attachments" not in _fm("2026-01-09_001240", [])


def test_wikilink_body_has_no_brackets():
    """Obsidian cannot parse [ or ] inside a wikilink, so Pull-Emails.ps1
    rewrites them at staging time. If that ever regresses, the link silently
    stops resolving in the vault rather than raising anywhere.
    """
    fm = _fm("2026-01-09_001240", ["2026-01-09_001240-01-Report(1).pdf"])
    inner = fm["attachments"][0][2:-2]
    assert "[" not in inner and "]" not in inner


# --- attachment promotion helpers (utils, re-exported by classify) ----------
# Substantive email attachments (decks, specs, spreadsheets) are promoted to
# reference notes so their content is searchable; images/logos/junk are not.

def test_is_promotable_attachment_accepts_office_and_pdf():
    for n in ["deck.pptx", "spec.docx", "sheet.xlsx", "concept.PDF",
              "old.ppt", "legacy.doc", "book.xls"]:
        assert ci.is_promotable_attachment(n), n


def test_is_promotable_attachment_rejects_images_and_junk():
    for n in ["image001.png", "logo.JPG", "photo.jpeg", "sig.gif",
              "cal.ics", "card.vcf", "smime.p7s", "notes.txt", "bundle.zip"]:
        assert not ci.is_promotable_attachment(n), n


def test_strip_attachment_prefix_removes_stamp_only():
    assert ci.strip_attachment_prefix(
        "2026-01-20_064632-04-Quarterly Roadmap EN"
    ) == "Quarterly Roadmap EN"
    # A name without the stamp prefix is returned unchanged.
    assert ci.strip_attachment_prefix("Plain Deck Name") == "Plain Deck Name"


# --- promote_email_attachments (the injection that builds docs entries) ------

def test_promote_email_attachments_builds_correct_doc_entries(tmp_path):
    """A pdf/Office attachment on a note-producing email becomes a docs entry
    with the right markers; images and attachment-less emails yield nothing."""
    kept = [
        {"output_filename": "2026-01-20-email-market-research.md",
         "date": "2026-01-20",
         "attachments": [
             "2026-01-20_064632-04-Quarterly Roadmap EN.pptx",  # promote
             "2026-01-20_064632-01-image001.png",               # skip (image)
             "2026-01-20_064839-01-Vendor Assessment.pdf",      # promote
         ]},
        {"output_filename": "2026-01-21-email-no-attachments.md",
         "date": "2026-01-21", "attachments": []},              # nothing
    ]
    docs = ci.promote_email_attachments(kept, tmp_path)
    assert len(docs) == 2, docs
    d0 = docs[0]
    assert d0["is_email_attachment"] is True
    assert d0["source_email"] == "2026-01-20-email-market-research"
    assert d0["date"] == "2026-01-20"
    assert d0["clean_stem"] == "Quarterly Roadmap EN"
    assert d0["filename"] == "2026-01-20_064632-04-Quarterly Roadmap EN.pptx"
    assert "_email-attachments" in d0["file"] and d0["filename"] in d0["file"]
    # output_filename is intentionally NOT set here (assigned by the docs pass).
    assert "output_filename" not in d0
    assert docs[1]["clean_stem"] == "Vendor Assessment"


def test_promote_email_attachments_empty_when_no_promotables(tmp_path):
    kept = [{"output_filename": "e.md", "date": "2026-01-20",
             "attachments": ["logo.png", "cal.ics", "smime.p7s"]}]
    assert ci.promote_email_attachments(kept, tmp_path) == []
