#!/usr/bin/env python3
"""
classify-inbox.py: Deterministic inbox classification, email header parsing,
thread grouping, pre-classification, batch planning, email body cleaning,
entity resolution, duplicate detection, and frontmatter pre-generation.

Replaces LLM-driven Phase 0 integrity check + Phase 1 of /w-daily.

Usage:
    python3 classify-inbox.py [--vault PATH] [--clean-bodies] [--staging-dir DIR]
                              [--thread-index PATH] [--resolve-entities]

Outputs JSON manifest to stdout. Logs to stderr.
"""

import argparse
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

# Add _scripts/ to path for shared utils
sys.path.insert(0, str(Path(__file__).parent))
from utils import (REPLY_PREFIXES, normalize_subject, subject_to_slug,
                   guess_wikilink_from_email, company_from_domain,
                   generate_pii_token, ensure_utf8_stdio, atomic_json_write,
                   apply_vip_boost, recipient_set,
                   OWNER_SLUG, OWNER_PERSONAL_EMAILS, LOCAL_TZ)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Markers that signal the start of a Teams/Webex meeting footer to strip.
# English only by default. If your org sends non-English email, add your locale's
# equivalents (e.g. the localized "Microsoft Teams meeting" / "Meeting ID:" text).
TEAMS_MARKERS = [
    "________________________________________________________________________________",
    "Microsoft Teams meeting",
    "Join: https://teams.microsoft.com/meet/",
    "Meeting ID:",
    "webex.com/msteams",
    "Join on a video conferencing device",
    "For organizers: Meeting options",
    "Need help?",
    "Org help",
    "IMPORTANT - DATA AND INFORMATION PROTECTION",
    "Company Logo [https://static.acme.example/",
    "Privacy and security",
    "Passcode:",
    # Add your locale's Teams footer markers here.
]

# Opening lines of corporate email disclaimers to strip. English only by default;
# add your org's localized disclaimer opening line(s) here.
DISCLAIMER_STARTS = [
    "DISCLAIMER:The contents of this email",
    "DISCLAIMER: The contents of this email",
    "IMPORTANT - DATA AND INFORMATION PROTECTION",
    # Add your locale's disclaimer opening line here.
]

SAFE_LINK_RE = re.compile(
    r"\[?(https://\w+\.safelinks\.protection\.outlook\.com/\?url=([^&\s\]]+)[^\s\]]*)\]?"
)

CID_RE = re.compile(r"\[cid:[^\]]*\]")

SIGNATURE_MARKERS = [
    "ACME CORP",
    "Acme Corp - Internal Division",
    "company-logo.png",
]

# Pre-classification keywords
HIGH_SUBJECT_KEYWORDS = [
    "prd", "okr", "planning", "status", "health check", "decision",
    "approved", "proposal", "budget", "escalat", "blocker", "urgent",
]

LOW_SIGNALS_BODY = [
    "placeholder for our regular",
    "running late",
    "can't make it",
    "thanks",
    "awesome",
    "will do",
    "+1",
    "forgot to attach",
    "here's the file",
    "fyi",
    "see below",
    "looping in",
    "worth checking",
]

# Outlook auto-notification subject prefixes. English only by default;
# add your locale's equivalent of "following:" here.
OUTLOOK_AUTO_PREFIXES = [
    "following:",   # English
]


# PII patterns for body sanitization
EMAIL_PII_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+[\w]")
PHONE_PII_RE = re.compile(r"\+\d{10,15}")


# ---------------------------------------------------------------------------
# PII sanitization
# ---------------------------------------------------------------------------

def load_sanitize_mappings(vault: Path) -> dict:
    """Load sanitize-mappings.json. Returns dict with emails, phones, token_to_pii."""
    path = vault / "_db" / "sanitize-mappings.json"
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"emails": {}, "phones": {}, "token_to_pii": {}}


def save_sanitize_mappings(vault: Path, mappings: dict) -> None:
    """Write sanitize-mappings.json atomically."""
    atomic_json_write(vault / "_db" / "sanitize-mappings.json", mappings)


def sanitize_body_pii(body: str, mappings: dict) -> str:
    """Replace email addresses and phone numbers in body text with tokens.

    Mutates mappings dict in-place when new PII is discovered.
    Returns sanitized body text.
    """
    existing_tokens = set(mappings.get("token_to_pii", {}).keys())

    def replace_email(m):
        addr = m.group(0).lower()
        token = mappings["emails"].get(addr)
        if not token:
            token = generate_pii_token("EMAIL", existing_tokens)
            mappings["emails"][addr] = token
            mappings["token_to_pii"][token] = addr
            existing_tokens.add(token)
        return token

    def replace_phone(m):
        phone = m.group(0)
        token = mappings["phones"].get(phone)
        if not token:
            token = generate_pii_token("PHONE", existing_tokens)
            mappings["phones"][phone] = token
            mappings["token_to_pii"][token] = phone
            existing_tokens.add(token)
        return token

    body = EMAIL_PII_RE.sub(replace_email, body)
    body = PHONE_PII_RE.sub(replace_phone, body)
    return body


# ---------------------------------------------------------------------------
# Email header parsing
# ---------------------------------------------------------------------------

def parse_email_headers(filepath: Path, preread_lines: list[str] | None = None) -> dict | None:
    """Parse Power Automate email headers. Returns dict or None if not an email.

    If preread_lines is provided, uses those instead of re-reading the file.
    """
    if preread_lines is not None:
        lines = preread_lines
    else:
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            return None

    if len(lines) < 3:
        return None

    filename = filepath.name
    is_sent = filename.startswith("SENT-")

    # Detect format: "Type " prefix (received) or plain (sent)
    has_type_prefix = any(
        line.startswith("Type ") for line in lines[:8]
    )

    headers = {}
    body_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            body_start = i + 1
            break

        if has_type_prefix and stripped.startswith("Type "):
            stripped = stripped[5:]  # Remove "Type " prefix

        colon_idx = stripped.find(":")
        if colon_idx > 0:
            key = stripped[:colon_idx].strip()
            val = stripped[colon_idx + 1:].strip()
            headers[key] = val

    # Validate this is actually an email
    has_from = "From" in headers
    has_subject = "Subject" in headers
    has_date = "Date" in headers

    if not (has_from and has_subject and has_date):
        return None

    # Parse recipients
    def split_recipients(field: str) -> list[str]:
        if not field:
            return []
        return [r.strip() for r in field.split(";") if r.strip()]

    to_list = split_recipients(headers.get("To", ""))
    cc_list = split_recipients(headers.get("CC", ""))

    # Parse date
    date_str = headers.get("Date", "")
    date_parsed = None
    try:
        # ISO format from Power Automate
        if "T" in date_str:
            date_parsed = date_str[:10]  # YYYY-MM-DD
        else:
            date_parsed = date_str[:10]
    except Exception:
        date_parsed = datetime.now().strftime("%Y-%m-%d")

    # Normalize subject
    subject = headers.get("Subject", "")
    normalized = normalize_subject(subject)

    # Direction
    direction = "sent" if is_sent or not headers.get("From", "").strip() else None

    # Body lines for pre-classification (5 for preview, up to 50 for HIGH signal scan)
    body_preview = []
    body_scan = []
    body_line_count = 0
    for line in lines[body_start:]:
        body_line_count += 1
        stripped = line.strip()
        if stripped:
            if len(body_preview) < 5:
                body_preview.append(stripped)
            if len(body_scan) < 50:
                body_scan.append(stripped)

    return {
        "file": str(filepath),
        "filename": filename,
        "from": headers.get("From", "").strip(),
        "to": to_list,
        "cc": cc_list,
        "subject": subject,
        "normalized_subject": normalized,
        "date": date_parsed,
        "conversation_id": headers.get("ConversationId", "").strip() or None,
        "category": headers.get("Category", "").strip() or None,
        "direction": direction,
        "recipient_count": len(to_list) + len(cc_list),
        "body_preview": body_preview,
        "body_scan": body_scan,
        "body_line_count": body_line_count,
        "body_start_line": body_start,
    }


# ---------------------------------------------------------------------------
# File classification
# ---------------------------------------------------------------------------

def _check_meeting_zone_content(content: str) -> bool:
    """Check if a meeting prep note has real content in its Meeting zone.

    Meeting zone = ## Discussion, ## Actions, ## Next time sections.
    Placeholders (-, - [ ], empty lines) don't count as content.
    """
    placeholders = {"", "-", "- [ ]", "- []"}
    in_meeting_zone = False
    past_prep_divider = False

    for line in content.split("\n"):
        stripped = line.strip()

        # The prep zone ends at the first --- after ## Prep
        if stripped == "---" and not past_prep_divider:
            past_prep_divider = True
            continue

        # Only look at content after the prep zone divider
        if not past_prep_divider:
            continue

        # Track meeting zone sections
        if stripped.startswith("## Discussion") or stripped.startswith("## Actions") or stripped.startswith("## Next time"):
            in_meeting_zone = True
            continue

        # Any other H2 exits the meeting zone
        if stripped.startswith("## ") and in_meeting_zone:
            in_meeting_zone = True  # Could be another meeting section
            continue

        if in_meeting_zone and stripped not in placeholders:
            return True

    return False


def parse_mr_json_metadata(json_path: Path) -> dict | None:
    """Read a Meeting Recorder .json companion for richer metadata.

    The .json is the canonical source produced by the recorder; the .txt is
    generated from it. When present, prefer json values over header parsing
    and pick up the screenshots, speakers map, and quality block. These
    don't appear in the .txt header at all.

    Returns a dict to merge into transcript metadata, or None on failure.
    Keys returned (any may be absent if not in the file):
        subject, date, meeting_datetime, meeting_type, attendees,
            recording_duration, overrides header values when present
        screenshots: list of {path, basename, timestamp_seconds, timestamp_str}
            with path resolved against the inbox _screenshots/ dir
        speakers_map: dict (voice-NNN -> display name), filled by app
            voice profile matcher once wired; empty today
        quality_flags: dict (truncated, truncation_reason, ...), emitted
            by app once quality tracking is wired; empty today
        companion_json: absolute string path to the .json
    """
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    out: dict = {"companion_json": str(json_path)}

    md = data.get("metadata") or {}
    if md.get("subject"):
        out["subject"] = str(md["subject"]).strip()
    if md.get("date"):
        date_val = str(md["date"])
        out["meeting_datetime"] = date_val
        out["date"] = date_val[:10] if len(date_val) >= 10 else date_val
    if md.get("meeting_type"):
        out["meeting_type"] = str(md["meeting_type"])
    if md.get("recording_duration"):
        out["recording_duration"] = str(md["recording_duration"])
    attendees = md.get("attendees")
    if isinstance(attendees, list):
        out["attendees"] = "; ".join(str(a) for a in attendees if a)
    elif isinstance(attendees, str):
        out["attendees"] = attendees

    # Screenshots: basenames in .json, resolve against sibling _screenshots/
    screenshots_dir = json_path.parent / "_screenshots"
    ann = data.get("annotations") or {}
    raw_shots = ann.get("screenshots") or []
    shots: list[dict] = []
    for s in raw_shots:
        basename = s.get("path") if isinstance(s, dict) else None
        if not basename:
            continue
        try:
            ts_secs = float(s.get("timestamp", 0))
        except (TypeError, ValueError):
            ts_secs = 0.0
        h = int(ts_secs // 3600)
        m = int((ts_secs % 3600) // 60)
        sec = int(ts_secs % 60)
        shots.append({
            "path": str(screenshots_dir / basename),
            "basename": basename,
            "timestamp_seconds": ts_secs,
            "timestamp_str": f"{h}:{m:02d}:{sec:02d}",
        })
    out["screenshots"] = shots

    # Speakers map: voice-NNN -> display name (empty until app A2 wires matcher)
    speakers = data.get("speakers")
    out["speakers_map"] = speakers if isinstance(speakers, dict) else {}

    # Quality flags (empty until app A3 emits them)
    quality = data.get("quality")
    out["quality_flags"] = quality if isinstance(quality, dict) else {}

    return out


def _parse_mr_md_frontmatter(fm_raw: str) -> dict:
    """Parse a Meeting Recorder .md frontmatter block into the mr_meta shape.

    The .md frontmatter mirrors the .txt header but uses YAML keys with hyphens
    (recording-duration, meeting-type, attendees as a YAML list, etc.). Returns
    the same dict shape that the .txt parser produces, so the rest of the pipeline
    (entity resolution, dedup, frontmatter generation) sees a consistent surface.
    """
    out: dict = {}
    current_key: str | None = None
    attendees: list[str] = []
    for raw_line in fm_raw.splitlines():
        line = raw_line.rstrip()
        if not line:
            current_key = None
            continue
        # List continuation: indented `- value`
        if line.startswith(("- ", "  - ")) and current_key == "attendees":
            val = line.lstrip("- ").strip().strip('"').strip("'")
            if val:
                attendees.append(val)
            continue
        # New key
        if ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip().strip('"').strip("'")
            current_key = key
            if key == "subject":
                out["subject"] = value
            elif key == "date":
                out["date"] = value[:10] if len(value) >= 10 else value
                out["meeting_datetime"] = value
            elif key == "meeting-type":
                out["meeting_type"] = value
            elif key == "recording-duration":
                out["recording_duration"] = value
            elif key in ("plaud-file-id", "plaudfileid"):
                out["plaud_file_id"] = value
            elif key == "calendar-subject":
                out["calendar_subject"] = value
            elif key == "calendar-organizer":
                out["calendar_organizer"] = value
            elif key == "calendar-match":
                out["calendar_match"] = value.lower() == "true"
            elif key == "attendees":
                if value:
                    # Inline list or single string
                    out["attendees"] = value
                # else: collect via list continuation
    if attendees:
        out["attendees"] = "; ".join(attendees)
    return out


def classify_file(filepath: Path) -> tuple[str, dict | None]:
    """
    Classify a single inbox file.
    Returns (type, metadata) where type is one of:
    skip, manual_note, manual_meeting, meeting_prep, email, transcript_mr,
    transcript_generic, document
    """
    name = filepath.name

    # Skip calendar JSON
    if name.endswith("-calendar.json") or name.endswith("_calendar.json"):
        return "skip", None

    ext = filepath.suffix.lower()

    # Markdown files: check frontmatter
    if ext == ".md":
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(8000)  # Read enough for frontmatter + meeting zone
        except Exception:
            return "document", None

        # Quick YAML frontmatter check
        if content.startswith("---"):
            fm_end = content.find("---", 3)
            if fm_end > 0:
                fm_raw = content[3:fm_end]
                fm = fm_raw.lower()
                if "type: manual-note" in fm:
                    return "manual_note", None
                if "type: meeting" in fm and "interaction-type: meeting" in fm:
                    if "meeting-prep: true" in fm:
                        has_content = _check_meeting_zone_content(content)
                        return "meeting_prep", {"has_meeting_content": has_content}

                    # Meeting Recorder .md detection: recording-duration in frontmatter
                    # OR timestamped speaker lines in body are signatures of MR output
                    # (vs Obsidian-template manual meetings, which have neither).
                    body_after_fm = content[fm_end + 3:]
                    has_recording_duration = "recording-duration:" in fm
                    has_timestamped_body = bool(
                        re.search(r"^\*?\*?\[\d+:\d+:\d+\]", body_after_fm, re.MULTILINE)
                    )
                    if has_recording_duration or has_timestamped_body:
                        # Parse MR metadata from frontmatter
                        mr_meta = _parse_mr_md_frontmatter(fm_raw)
                        # Merge .json companion (canonical source)
                        json_companion = filepath.with_suffix(".json")
                        if json_companion.exists():
                            json_meta = parse_mr_json_metadata(json_companion)
                            if json_meta:
                                mr_meta.update(json_meta)
                        subject = mr_meta.get("subject", "")
                        mr_meta["is_recovered"] = subject.startswith("[Recovered]")
                        mr_meta["is_zero_duration"] = mr_meta.get("recording_duration", "") == "0:00:00"
                        mr_meta["source_format"] = "md"  # signal: no .txt, body in .md
                        return "transcript_mr", {"file": str(filepath), **mr_meta}

                    return "manual_meeting", None
        return "document", None

    # .eml / .msg
    if ext in (".eml", ".msg"):
        return "email", parse_email_headers(filepath)

    # .txt files: could be email, transcript, or document
    if ext == ".txt":
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
        except Exception:
            return "document", None

        first_lines = all_lines[:8]
        first_text = "".join(first_lines)

        # Meeting Recorder transcript
        if ("MeetingSubject:" in first_text
                and "MeetingDate:" in first_text
                and "Attendees:" in first_text):
            # Extract basic metadata from headers (read up to 12 lines for Plaud extra headers)
            mr_meta = {}
            for line in all_lines[:12]:
                stripped = line.strip()
                if not stripped:
                    break
                if stripped.startswith("MeetingSubject:"):
                    mr_meta["subject"] = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("MeetingDate:"):
                    date_val = stripped.split(":", 1)[1].strip()
                    mr_meta["date"] = date_val[:10] if len(date_val) >= 10 else date_val
                    mr_meta["meeting_datetime"] = date_val
                elif stripped.startswith("MeetingType:"):
                    mr_meta["meeting_type"] = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("Attendees:"):
                    mr_meta["attendees"] = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("RecordingDuration:"):
                    mr_meta["recording_duration"] = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("PlaudFileId:"):
                    mr_meta["plaud_file_id"] = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("CalendarMatch:"):
                    mr_meta["calendar_match"] = stripped.split(":", 1)[1].strip().lower() == "true"
                elif stripped.startswith("CalendarSubject:"):
                    mr_meta["calendar_subject"] = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("CalendarOrganizer:"):
                    mr_meta["calendar_organizer"] = stripped.split(":", 1)[1].strip()
            # Merge richer metadata from .json companion when present. The .json is
            # the canonical source: recorder generates the .txt from it. Json values
            # override the header for shared fields and add screenshots, speakers map,
            # and quality flags that the header doesn't carry.
            json_companion = filepath.with_suffix(".json")
            if json_companion.exists():
                json_meta = parse_mr_json_metadata(json_companion)
                if json_meta:
                    mr_meta.update(json_meta)
            # Detect recovered transcripts (after merge so the latest subject wins)
            subject = mr_meta.get("subject", "")
            mr_meta["is_recovered"] = subject.startswith("[Recovered]")
            mr_meta["is_zero_duration"] = mr_meta.get("recording_duration", "") == "0:00:00"
            return "transcript_mr", {"file": str(filepath), **mr_meta}

        # Email check
        has_from = any(
            l.strip().startswith("From:") or l.strip().startswith("Type From:")
            for l in first_lines
        )
        has_subject = any(
            l.strip().startswith("Subject:") or l.strip().startswith("Type Subject:")
            for l in first_lines
        )
        has_date = any(
            l.strip().startswith("Date:") or l.strip().startswith("Type Date:")
            for l in first_lines
        )

        if has_from and has_subject and has_date:
            return "email", parse_email_headers(filepath, preread_lines=all_lines)

        # Generic transcript: timestamps + speaker labels
        timestamp_pattern = re.compile(r"\[\d+:\d+:\d+\]|\(\d+:\d+\)")
        speaker_pattern = re.compile(r"^[\w\-]+:")
        has_timestamps = any(timestamp_pattern.search(l) for l in first_lines)
        has_speakers = any(speaker_pattern.match(l.strip()) for l in first_lines[2:])

        if has_timestamps and has_speakers:
            return "transcript_generic", {"file": str(filepath)}

        return "document", None

    # PDF, DOCX, PPTX, XLSX, HTML
    if ext in (".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm"):
        return "document", None

    # Unknown
    return "document", None


# ---------------------------------------------------------------------------
# Pre-classification (relevance hints)
# ---------------------------------------------------------------------------

def pre_classify(email: dict) -> tuple[str, str | None]:
    """Quick relevance pre-score using headers + body preview.

    Returns (relevance, reason) where:
    - relevance: "low-definitive", "low", "medium", "high"
    - reason: human-readable reason for low-definitive scores (None otherwise)

    "low-definitive" means the email can be skipped without LLM review.
    """
    subject_lower = email.get("normalized_subject", "")
    subject_raw = email.get("subject", "").lower()
    body_preview = email.get("body_preview", [])
    body_text = " ".join(body_preview).lower()
    body_lines = email.get("body_line_count", 0)
    to_list = [addr.lower() for addr in email.get("to", [])]
    direction = email.get("direction")

    # --- Definitive LOW: patterns that NEVER need LLM review ---

    # Self-forwards to personal email
    if direction == "sent" and to_list and all(addr in OWNER_PERSONAL_EMAILS for addr in to_list):
        return "low-definitive", "Self-forward to personal email"

    # Outlook auto-notifications ("Following:")
    if any(subject_raw.startswith(prefix) for prefix in OUTLOOK_AUTO_PREFIXES):
        if body_lines < 10:
            return "low-definitive", "Outlook auto-notification (following/tracking)"

    # Pure acknowledgments: very short body with only logistical content
    if body_lines < 3 and body_text.strip():
        body_stripped = body_text.strip()
        ack_phrases = {"thanks", "thank you", "awesome", "will do", "ok", "+1",
                       "got it", "noted", "sounds good", "perfect", "great"}
        if body_stripped in ack_phrases or (len(body_stripped) < 20 and any(
                body_stripped.startswith(p) for p in ack_phrases)):
            return "low-definitive", "Pure acknowledgment"

    # --- Heuristic LOW (still sent to agent for confirmation) ---
    if body_lines < 3:
        if any(sig in body_text for sig in LOW_SIGNALS_BODY):
            return "low", None
        if "teams.microsoft.com" in body_text or "webex" in body_text:
            return "low", None

    # --- HIGH signals from subject ---
    if any(kw in subject_lower for kw in HIGH_SUBJECT_KEYWORDS):
        return "high", None

    # HIGH signals from body (scan up to 50 lines, not just preview)
    body_scan = email.get("body_scan", body_preview)  # fall back to preview
    body_text_extended = " ".join(body_scan).lower() if body_scan else body_text
    high_body_keywords = [
        "aligned", "agreed", "decided", "approved", "proposal",
        "pushing back", "flagging", "concern", "not aligned",
        "please ", "can you ", "action needed",
        "urgent", "blocker", "risk", "gap",
    ]
    if any(kw in body_text_extended for kw in high_body_keywords):
        return "high", None

    # Check if Sam wrote substantive content (sent email with many lines)
    if direction == "sent" and body_lines > 5:
        return "high", None

    return "medium", None


# ---------------------------------------------------------------------------
# Thread grouping
# ---------------------------------------------------------------------------

def group_threads(emails: list[dict]) -> list[dict]:
    """Group emails into threads by ConversationId or normalized subject."""
    conv_groups: dict[str, list[dict]] = {}
    subject_groups: dict[str, list[dict]] = {}
    no_conv_emails = []

    for email in emails:
        conv_id = email.get("conversation_id")
        if conv_id:
            conv_groups.setdefault(conv_id, []).append(email)
        else:
            no_conv_emails.append(email)

    # Merge no-conv emails by subject into conv groups if subject matches
    for email in no_conv_emails:
        norm_subj = email.get("normalized_subject", "")
        merged = False
        for conv_id, group in conv_groups.items():
            if any(e.get("normalized_subject") == norm_subj for e in group):
                group.append(email)
                merged = True
                break
        if not merged:
            subject_groups.setdefault(norm_subj, []).append(email)

    threads = []
    for conv_id, group in conv_groups.items():
        threads.append({
            "thread_id": f"conv:{conv_id[:20]}",
            "conversation_id": conv_id,
            "emails": sorted(group, key=lambda e: e.get("date", "")),
        })
    for subj, group in subject_groups.items():
        threads.append({
            "thread_id": f"subj:{subj[:40]}",
            "conversation_id": None,
            "emails": sorted(group, key=lambda e: e.get("date", "")),
        })

    return threads


# ---------------------------------------------------------------------------
# Batch planning
# ---------------------------------------------------------------------------

def plan_batches(threads: list[dict], max_batch: int = 20) -> list[dict]:
    """Plan thread-aware batches. Threads never split across batches."""
    # Sort threads by earliest email date
    threads_sorted = sorted(
        threads,
        key=lambda t: min(e.get("date", "") for e in t["emails"])
    )

    batches = []
    current_batch_files = []
    current_batch_threads = {}

    for thread in threads_sorted:
        thread_files = [e["file"] for e in thread["emails"]]

        # If single thread exceeds max, give it its own batch
        if len(thread_files) > max_batch:
            if current_batch_files:
                batches.append({
                    "files": current_batch_files,
                    "thread_groups": current_batch_threads,
                })
                current_batch_files = []
                current_batch_threads = {}
            batches.append({
                "files": thread_files,
                "thread_groups": {thread["thread_id"]: thread_files},
            })
            continue

        # Check if adding this thread exceeds batch size
        if len(current_batch_files) + len(thread_files) > max_batch:
            batches.append({
                "files": current_batch_files,
                "thread_groups": current_batch_threads,
            })
            current_batch_files = []
            current_batch_threads = {}

        current_batch_files.extend(thread_files)
        current_batch_threads[thread["thread_id"]] = thread_files

    if current_batch_files:
        batches.append({
            "files": current_batch_files,
            "thread_groups": current_batch_threads,
        })

    return batches


# ---------------------------------------------------------------------------
# Email body cleaning
# ---------------------------------------------------------------------------

def clean_email_body(filepath: Path, body_start_line: int) -> None:
    """Clean email body in-place: strip Teams footers, disclaimers, etc."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return

    headers = lines[:body_start_line]
    body_lines = lines[body_start_line:]
    body = "".join(body_lines)

    # 1. Strip Teams/Webex footers: find earliest marker and truncate
    earliest_marker = len(body)
    for marker in TEAMS_MARKERS:
        idx = body.find(marker)
        if idx >= 0 and idx < earliest_marker:
            earliest_marker = idx
    if earliest_marker < len(body):
        body = body[:earliest_marker]

    # 2. Strip disclaimers (cut everything from disclaimer to end of body)
    for disclaimer in DISCLAIMER_STARTS:
        idx = body.find(disclaimer)
        if idx >= 0:
            body = body[:idx]

    # 3. Simplify safe links
    def decode_safe_link(m):
        encoded_url = m.group(2)
        try:
            return urllib.parse.unquote(encoded_url)
        except Exception:
            return m.group(0)

    body = SAFE_LINK_RE.sub(decode_safe_link, body)

    # 4. Strip [cid:...] references
    body = CID_RE.sub("", body)

    # 5. Strip signature blocks (look for ACME CORP pattern)
    for sig in SIGNATURE_MARKERS:
        idx = body.find(sig)
        if idx >= 0:
            # Check if this looks like a signature block (near end of content)
            remaining = body[idx:]
            if len(remaining) < 500:  # Signature-sized block
                body = body[:idx]
                break

    # 6. Whitespace cleanup
    # Collapse 3+ blank lines to 2
    body = re.sub(r"\n{4,}", "\n\n\n", body)
    # Trim trailing whitespace per line
    body = "\n".join(line.rstrip() for line in body.split("\n"))
    # Remove lines that are only ***** or dashes
    body = re.sub(r"^\*{5,}\s*$", "", body, flags=re.MULTILINE)
    body = re.sub(r"^-{5,}\s*$", "", body, flags=re.MULTILINE)
    # Final trim
    body = body.strip()

    # Write back (atomic: write to tmp, then replace)
    try:
        tmp = filepath.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(headers)
            if not body.startswith("\n"):
                f.write("\n")
            f.write(body)
            f.write("\n")
        os.replace(str(tmp), str(filepath))
    except Exception as e:
        print(f"Warning: Could not write cleaned body for {filepath}: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------

def load_email_lookup(vault: Path) -> dict:
    """Load email-lookup.json for O(1) email→wikilink+VIP resolution."""
    path = vault / "_db" / "email-lookup.json"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def resolve_email_addr(addr: str, lookup: dict) -> dict:
    """Resolve a single email address to wikilink + VIP.

    Returns: {wikilink, vip, resolved: True} or {wikilink (guessed), resolved: False}
    """
    addr_lower = addr.strip().lower()
    if not addr_lower:
        return {"wikilink": "", "resolved": False}
    entry = lookup.get(addr_lower)
    if entry:
        result = {"wikilink": entry["wikilink"], "resolved": True}
        if "vip" in entry:
            result["vip"] = entry["vip"]
        return result
    # Guess from email address
    return {
        "wikilink": guess_wikilink_from_email(addr_lower),
        "email": addr_lower,
        "company": company_from_domain(addr_lower),
        "resolved": False,
    }


def resolve_participants(email_meta: dict, lookup: dict) -> None:
    """Resolve From/To/CC to wikilinks and detect VIP involvement.

    Mutates email_meta in-place, adding:
      resolved_from: {wikilink, vip?, resolved}
      resolved_to: [{wikilink, vip?, resolved}, ...]
      resolved_cc: [{wikilink, vip?, resolved}, ...]
      vip_involved: ["boss-chain", ...]  (distinct tiers)
      vip_tags: ["vip/boss-chain", ...]
      unresolved_entities: [{name, email, company}, ...]
    """
    from_addr = email_meta.get("from", "")
    to_addrs = email_meta.get("to", [])
    cc_addrs = email_meta.get("cc", [])

    email_meta["resolved_from"] = resolve_email_addr(from_addr, lookup)
    email_meta["resolved_to"] = [resolve_email_addr(a, lookup) for a in to_addrs]
    email_meta["resolved_cc"] = [resolve_email_addr(a, lookup) for a in cc_addrs]

    # Collect VIP tiers with position tracking for relevance boost
    vip_tiers = set()
    vip_positions = {}  # tier -> best position ("from_to" or "cc")
    from_to_resolved = [email_meta["resolved_from"]] + email_meta["resolved_to"]
    cc_resolved = email_meta["resolved_cc"]
    for r in from_to_resolved:
        if "vip" in r:
            vip_tiers.add(r["vip"])
            vip_positions[r["vip"]] = "from_to"
    for r in cc_resolved:
        if "vip" in r:
            vip_tiers.add(r["vip"])
            if r["vip"] not in vip_positions:  # don't downgrade from_to to cc
                vip_positions[r["vip"]] = "cc"

    # "team" tier = no VIP marking per vip.md rules (high-volume daily collab)
    vip_tiers.discard("team")
    vip_positions.pop("team", None)
    email_meta["vip_involved"] = sorted(vip_tiers)
    email_meta["vip_tags"] = [f"vip/{t}" for t in sorted(vip_tiers)]
    email_meta["vip_positions"] = vip_positions

    # Collect unresolved entities for stub creation
    unresolved = []
    for r in from_to_resolved + cc_resolved:
        if not r.get("resolved") and r.get("email"):
            unresolved.append({
                "email": r["email"],
                "wikilink": r["wikilink"],
                "company": r.get("company", ""),
            })
    email_meta["unresolved_entities"] = unresolved


def resolve_transcript_attendees(transcript_meta: dict, lookup: dict) -> None:
    """Resolve transcript attendee emails to wikilinks and detect VIP.

    Mutates transcript_meta in-place, adding:
      resolved_attendees: [{wikilink, vip?, resolved}, ...]
      vip_involved: [...]
      vip_tags: [...]
      unresolved_entities: [...]
    """
    attendees_str = transcript_meta.get("attendees", "")
    if not attendees_str or attendees_str == "recovered":
        transcript_meta["resolved_attendees"] = []
        transcript_meta["vip_involved"] = []
        transcript_meta["vip_tags"] = []
        transcript_meta["unresolved_entities"] = []
        return

    addrs = [a.strip() for a in attendees_str.split(";") if a.strip()]
    resolved = [resolve_email_addr(a, lookup) for a in addrs]
    transcript_meta["resolved_attendees"] = resolved

    vip_tiers = set()
    unresolved = []
    for r in resolved:
        if "vip" in r:
            vip_tiers.add(r["vip"])
        if not r.get("resolved") and r.get("email"):
            unresolved.append({
                "email": r["email"],
                "wikilink": r["wikilink"],
                "company": r.get("company", ""),
            })

    # "team" tier = no VIP marking per vip.md rules (high-volume daily collab)
    vip_tiers.discard("team")
    transcript_meta["vip_involved"] = sorted(vip_tiers)
    transcript_meta["vip_tags"] = [f"vip/{t}" for t in sorted(vip_tiers)]
    transcript_meta["unresolved_entities"] = unresolved


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def load_ingest_log(vault: Path) -> dict:
    """Load ingest-log.json and return lookup structures.

    Returns: {
        by_source: {source-file: entry},
        by_key: {"{normalized_subject}|{date}": [entry, ...]}
    }

    by_key indexes only created email entries (the ones with an output note to
    compare against) for cheap (subject, date) candidate filtering during the
    content-dedup confirmation step in check_already_processed.
    """
    path = vault / "_db" / "ingest-log.json"
    if not path.exists():
        return {"by_source": {}, "by_key": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            log = json.load(f)
    except Exception:
        return {"by_source": {}, "by_key": {}}

    by_source = {}
    by_key: dict[str, list] = {}
    for entry in log:
        sf = entry.get("source-file", "")
        if sf:
            by_source[sf] = entry
        if entry.get("type") == "email" and entry.get("action") == "created":
            key = f"{normalize_subject(entry.get('subject', ''))}|{entry.get('date', '')}"
            by_key.setdefault(key, []).append(entry)

    return {"by_source": by_source, "by_key": by_key}


def _read_note_recipients(path: Path) -> frozenset:
    """Read `to` + `cc` wikilinks from a note's YAML frontmatter (stdlib only).

    Manual parse (consistent with the rest of the pipeline) of the two list
    fields; returns a normalized recipient_set for comparison.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return frozenset()
    if not text.startswith("---"):
        return frozenset()
    end = text.find("\n---", 3)
    block = text[3:end] if end != -1 else text[3:]

    recipients = []
    current = None  # "to" / "cc" while inside one of those list fields
    for line in block.split("\n"):
        if not line.strip():
            continue
        if not line[0].isspace():  # new top-level key ends any active list
            key = line.split(":", 1)[0].strip()
            current = key if key in ("to", "cc") else None
            continue
        if current:
            item = line.strip().lstrip("-").strip().strip('"').strip("'")
            if item:
                recipients.append(item)
    return recipient_set(recipients)


def check_already_processed(email_meta: dict, ingest_log: dict, vault: Path) -> str | None:
    """Check if email was already processed.

    Returns reason string if should skip, None if should process.

    Two-stage: (1) exact source-filename match via by_source (cheap, unchanged);
    (2) on a filename miss, content-dedup: a re-pulled email under a different
    filename. Candidates are filtered by (normalized subject, date) via by_key,
    then confirmed only when the recipient sets are EQUAL and non-empty. Distinct
    emails sharing a subject+date have different recipients and MUST NOT be
    dropped (false dedup silently loses a real email).
    """
    filename = email_meta.get("filename", "")
    by_source = ingest_log.get("by_source", {})

    entry = by_source.get(filename)
    if entry:
        action = entry.get("action", "")
        if action.startswith("skipped"):
            return f"already-{action}"
        if action == "created":
            output = entry.get("output-file")
            if output and (vault / output).exists():
                return "already-processed"
            # Ghost entry, output file missing, process normally
            return None
        return None

    # by_source MISS → content-dedup against same-subject+date created notes.
    key = f"{normalize_subject(email_meta.get('subject', ''))}|{email_meta.get('date', '')}"
    candidates = ingest_log.get("by_key", {}).get(key, [])
    if not candidates:
        return None

    cur_to = [r["wikilink"] for r in email_meta.get("resolved_to", []) if r.get("wikilink")]
    cur_cc = [r["wikilink"] for r in email_meta.get("resolved_cc", []) if r.get("wikilink")]
    cur_recipients = recipient_set(cur_to + cur_cc)
    if not cur_recipients:
        return None  # nothing reliable to compare on, never dedup

    for cand in candidates:
        if cand.get("source-file") == filename:
            continue  # already handled by by_source
        output = cand.get("output-file")
        if not output:
            continue
        note_path = vault / output
        if not note_path.exists():
            continue  # ghost entry
        if _read_note_recipients(note_path) == cur_recipients:
            return f"already-processed (content dup of {output})"

    return None


# ---------------------------------------------------------------------------
# Frontmatter pre-generation
# ---------------------------------------------------------------------------

def generate_email_frontmatter(email_meta: dict) -> dict:
    """Generate complete email frontmatter dict from pre-resolved metadata.

    All deterministic fields. LLM-dependent fields (summary, project, status)
    are set to placeholder values that the agent fills in.
    """
    fm = {
        "date": email_meta.get("date", ""),
        "type": "email",
        "interaction-type": "email",
        "from": email_meta.get("resolved_from", {}).get("wikilink", ""),
        "to": [r["wikilink"] for r in email_meta.get("resolved_to", [])],
        "subject": email_meta.get("subject", ""),
        "summary": "",  # LLM fills this
    }

    # CC (only if non-empty)
    cc = [r["wikilink"] for r in email_meta.get("resolved_cc", []) if r.get("wikilink")]
    if cc:
        fm["cc"] = cc

    # Optional fields
    cat = email_meta.get("category")
    if cat and cat.lower() != "uncategorized":
        fm["email-category"] = cat

    conv_id = email_meta.get("conversation_id")
    if conv_id:
        fm["conversation-id"] = conv_id

    if email_meta.get("direction") == "sent":
        fm["direction"] = "sent"

    # Apply VIP relevance boost (Step 3.5 from email-preprocessing rules)
    base_relevance = email_meta.get("pre_relevance", "medium")
    vip = email_meta.get("vip_involved", [])
    vip_pos = email_meta.get("vip_positions", {})
    fm["relevance"] = apply_vip_boost(base_relevance, vip, vip_pos)

    if vip:
        fm["vip-involved"] = vip
        fm["tags"] = email_meta.get("vip_tags", [])

    fm["source-file"] = email_meta.get("filename", "")

    return fm


def generate_email_filename(email_meta: dict) -> str:
    """Generate output filename for an email note."""
    date = email_meta.get("date", "")
    subject = email_meta.get("subject", "")
    slug = subject_to_slug(subject)
    return f"{date}-email-{slug}.md"


def generate_transcript_frontmatter(transcript_meta: dict) -> dict:
    """Generate complete transcript frontmatter dict from pre-resolved metadata."""
    meeting_type = transcript_meta.get("meeting_type", "general")

    # Determine if 1on1: exactly 2 resolved attendees (one is Sam)
    resolved = transcript_meta.get("resolved_attendees", [])
    owner_links = {"[[Sam-Rivera]]"}
    non_owner = [r for r in resolved if r.get("wikilink") not in owner_links]
    is_1on1 = len(non_owner) == 1 and len(resolved) <= 3  # Allow some tolerance

    if is_1on1:
        meeting_type = "1on1"

    fm = {
        "date": transcript_meta.get("date", ""),
        "type": "meeting",
        "interaction-type": "meeting",
        "meeting-type": meeting_type,
        "summary": "",  # LLM fills this
        "attendees": [r["wikilink"] for r in resolved if r.get("wikilink")],
    }

    if is_1on1 and non_owner:
        fm["person"] = non_owner[0]["wikilink"]

    duration = transcript_meta.get("recording_duration")
    if duration:
        # Pad to HH:MM:SS format per spec
        parts = duration.split(":")
        if len(parts) == 3 and len(parts[0]) == 1:
            duration = f"0{duration}"
        fm["recording-duration"] = duration

    vip = transcript_meta.get("vip_involved", [])
    if vip:
        fm["vip-involved"] = vip
        fm["tags"] = transcript_meta.get("vip_tags", [])

    # Surface quality flags from the .json companion (currently empty until app
    # writes them, but the shape is defined). truncated=true → mark frontmatter
    # so reviewers see the recording wasn't complete.
    quality_flags = transcript_meta.get("quality_flags") or {}
    if quality_flags.get("truncated"):
        fm["recording-quality"] = "truncated"
        reason = quality_flags.get("truncation_reason")
        if reason:
            fm["recording-quality-reason"] = reason

    fm["source-file"] = Path(transcript_meta.get("file", "")).name

    return fm


def generate_transcript_filename(transcript_meta: dict, frontmatter: dict) -> str:
    """Generate output filename for a transcript note."""
    date = transcript_meta.get("date", "")
    meeting_type = frontmatter.get("meeting-type", "general")
    subject = transcript_meta.get("subject", "")
    slug = subject_to_slug(subject)
    # Strip meeting-type prefix from slug if already present (prevents "1on1-1on1-")
    if slug.startswith(f"{meeting_type}-"):
        slug = slug[len(meeting_type) + 1:]
    return f"{date}-{meeting_type}-{slug}.md"


# ---------------------------------------------------------------------------
# Run-time estimate (ETA) for the /w-daily heads-up and lite mode
# ---------------------------------------------------------------------------

# Per-item wall-clock estimates in minutes. Rough, tuned against the timing
# notes in w-daily/SKILL.md (inline email ~30s, email agent batch ~6min,
# transcript synthesis ~6min, long ~12min, low-stakes ~5min). Centralized here
# so they can be adjusted against observed reality without touching logic.
ETA_EMAIL_INLINE = 0.5          # whole inline email batch (<=6 high/med, simple threads)
ETA_EMAIL_AGENT_BATCH = 6.0     # one email-processor agent batch
ETA_DOC = 0.3                   # per document
ETA_TRANSCRIPT_SUBSTANTIVE = 6.0
ETA_TRANSCRIPT_LONG = 12.0      # substantive + recording longer than ETA_LONG_RECORDING_SEC
ETA_TRANSCRIPT_LOW = 5.0        # low-stakes (lecture/training/webinar/demo)
ETA_LONG_RECORDING_SEC = 30 * 60
ETA_CONTENTION_FACTOR = 0.5     # >2 transcripts run in parallel but share one
                                # Sonnet throughput pool, so wall-clock grows
                                # with the rest, not just the slowest
ETA_SLOW_THRESHOLD_MIN = 2.0    # below this, /w-daily prints no heads-up line
ETA_DEFER_OFFER_MIN_TRANSCRIPTS = 3  # at/above this transcript count, a normal
                                     # (non-lite) run also pauses once for the
                                     # synthesize/defer choice. Distinct from
                                     # ETA_SLOW_THRESHOLD_MIN (the slow heads-up,
                                     # which trips on a single transcript and is
                                     # too sensitive to gate deferral on).

LOW_STAKES_SUBJECT_PREFIXES = (
    "lecture", "training", "webinar", "tutorial", "onboarding", "demo",
    "demonstration", "walkthrough",
)
# Subjects whose LAST word is one of these are low-stakes even when the marker
# is not the prefix (e.g. "... System Demonstration"). Kept deliberately narrow:
# matching markers mid-subject would wrongly defer real meetings (e.g. a budget
# review whose title merely contains "training").
LOW_STAKES_SUBJECT_TRAILERS = frozenset({"demonstration", "demo", "walkthrough"})

# Strip a leading date token and a leading generic meeting prefix before matching,
# so "06-30 Meeting: Tool Demonstration" normalizes to "tool demonstration".
_STAKES_DATE_PREFIX = re.compile(r"^(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2})\s+")
_STAKES_GENERIC_PREFIX = re.compile(r"^(?:weekly\s+)?(?:meeting|sync|call)\s*[:\-]?\s+")


def _normalize_subject_for_stakes(subject: str) -> str:
    s = (subject or "").strip().lower()
    s = _STAKES_DATE_PREFIX.sub("", s)
    s = _STAKES_GENERIC_PREFIX.sub("", s)
    return s.strip()


def _duration_to_seconds(duration: str) -> int:
    """Parse 'H:MM:SS' or 'MM:SS' into seconds. Returns 0 on failure."""
    if not duration:
        return 0
    try:
        nums = [int(p) for p in duration.split(":")]
    except ValueError:
        return 0
    if len(nums) == 3:
        h, m, s = nums
    elif len(nums) == 2:
        h, m, s = 0, nums[0], nums[1]
    else:
        return 0
    return h * 3600 + m * 60 + s


def classify_transcript_stakes(transcript_meta: dict) -> str:
    """Classify a transcript as 'low-stakes' or 'substantive'.

    Low-stakes = a passive knowledge-transfer recording the owner can safely
    defer: the (normalized) subject begins with a learning marker, OR its last
    word is a demo/walkthrough noun, AND it is not a 1on1 or steerco. Mirrors
    the Model-triage signal documented in w-daily/SKILL.md. 'Passive attendee'
    can't be known before synthesis, so meeting-type (1on1/steerco are never
    low-stakes) stands in as a deterministic proxy.

    Conservative by design: marker matching is anchored to the prefix or the
    trailing noun, never mid-subject, so a real meeting whose title merely
    contains a marker word (e.g. "Q3 training budget review") stays substantive.
    """
    meeting_type = (transcript_meta.get("meeting_type") or "general").lower()
    if meeting_type in ("1on1", "steerco"):
        return "substantive"
    subject = _normalize_subject_for_stakes(transcript_meta.get("subject", ""))
    if not subject:
        return "substantive"
    if subject.startswith(LOW_STAKES_SUBJECT_PREFIXES):
        return "low-stakes"
    if subject.split()[-1] in LOW_STAKES_SUBJECT_TRAILERS:
        return "low-stakes"
    return "substantive"


def estimate_transcript_minutes(transcript_meta: dict, stakes: str) -> float:
    if stakes == "low-stakes":
        return ETA_TRANSCRIPT_LOW
    if _duration_to_seconds(transcript_meta.get("recording_duration", "")) > ETA_LONG_RECORDING_SEC:
        return ETA_TRANSCRIPT_LONG
    return ETA_TRANSCRIPT_SUBSTANTIVE


def estimate_eta(result: dict, counts: dict) -> dict:
    """Rough wall-clock estimate for a full run and a lite run.

    full = emails + docs + transcripts(synthesized).
    lite = emails + docs only (all transcripts deferred to thin stubs, ~free).
    Transcripts dominate: <=2 run in one sequential agent (sum); >2 run in
    parallel but contend on one Sonnet pool (slowest + a fraction of the rest).
    Also stamps each transcript with its 'stakes' class and 'est_minutes'.
    """
    breakdown = []

    # Emails: inline when <=6 high/med and no thread wider than 3, else agents.
    n_high_med = len(result.get("email_manifest", []))
    email_min = 0.0
    if n_high_med:
        max_thread = max((len(t.get("emails", [])) for t in result.get("threads", [])), default=0)
        if n_high_med <= 6 and max_thread <= 3:
            email_min = ETA_EMAIL_INLINE
        else:
            n_batches = max(1, len(result.get("batches", [])))
            email_min = ETA_EMAIL_AGENT_BATCH * n_batches
        breakdown.append({"kind": "emails", "count": n_high_med, "est_minutes": round(email_min, 1)})

    # Docs.
    n_docs = counts.get("docs", 0)
    doc_min = ETA_DOC * n_docs
    if n_docs:
        breakdown.append({"kind": "docs", "count": n_docs, "est_minutes": round(doc_min, 1)})

    # Transcripts.
    t_estimates = []
    for t in result.get("transcripts", []):
        stakes = classify_transcript_stakes(t)
        t["stakes"] = stakes
        est = estimate_transcript_minutes(t, stakes)
        t["est_minutes"] = est
        t_estimates.append(est)
        name = t.get("output_filename") or t.get("subject") or t.get("file") or ""
        breakdown.append({
            "kind": "transcript",
            "name": Path(name).stem if name else "",
            "class": stakes,
            "est_minutes": est,
        })

    n_t = len(t_estimates)
    if n_t == 0:
        transcript_min = 0.0
    elif n_t <= 2:
        transcript_min = sum(t_estimates)  # one agent, sequential
    else:
        ordered = sorted(t_estimates, reverse=True)
        transcript_min = ordered[0] + ETA_CONTENTION_FACTOR * sum(ordered[1:])

    full_min = email_min + doc_min + transcript_min
    lite_min = email_min + doc_min  # transcripts deferred, negligible script cost

    return {
        "full_minutes": int(round(full_min)),
        "lite_minutes": int(round(lite_min)),
        "slow": full_min > ETA_SLOW_THRESHOLD_MIN,
        "transcript_count": n_t,
        # A normal run also pauses for the synthesize/defer choice at/above the
        # threshold. Kept independent of `slow` (which trips on a single slow item).
        "defer_offer": n_t >= ETA_DEFER_OFFER_MIN_TRANSCRIPTS,
        "breakdown": breakdown,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Classify inbox files for /w-daily")
    parser.add_argument("--vault", default=str(Path(__file__).resolve().parent.parent),
                        help="Vault root directory")
    parser.add_argument("--clean-bodies", action="store_true",
                        help="Clean email bodies in-place after classification")
    parser.add_argument("--staging-dir", default=None,
                        help="Override inbox dir (e.g., 00-Inbox/_processing/)")
    parser.add_argument("--thread-index", default=None,
                        help="Path to thread-index.json for cross-batch thread lookup")
    parser.add_argument("--manifest-file", default=None,
                        help="Override manifest file path (default: _db/manifest.json). "
                             "Full manifest always written to file, compact summary to stdout.")
    parser.add_argument("--sanitize-pii", action="store_true",
                        help="Replace email addresses and phone numbers in email bodies "
                             "with tokens from _db/sanitize-mappings.json")
    parser.add_argument("--resolve-entities", action="store_true",
                        help="Resolve email entities via email-lookup.json, "
                             "check duplicates via ingest-log, "
                             "generate frontmatter and filenames")
    args = parser.parse_args()

    vault = Path(args.vault)
    inbox = Path(args.staging_dir) if args.staging_dir else vault / "00-Inbox"

    # Resume check: if _processing/ exists and has files, use that
    processing_dir = vault / "00-Inbox" / "_processing"
    resumed_files = []
    if processing_dir.exists() and any(processing_dir.iterdir()):
        resumed_files = list(processing_dir.iterdir())
        print(f"Resuming {len(resumed_files)} files from previous interrupted run.", file=sys.stderr)
        # Use _processing as the source
        inbox = processing_dir

    # Collect all files
    files = []
    if inbox.exists():
        for f in inbox.iterdir():
            if f.is_file() and f.name != ".DS_Store":
                files.append(f)

    # Also check main inbox if we're looking at _processing
    if inbox == processing_dir:
        main_inbox = vault / "00-Inbox"
        for f in main_inbox.iterdir():
            if f.is_file() and f.name != ".DS_Store" and f.name != "_processing":
                files.append(f)

    # Classify all files
    result = {
        "manual_notes": [],
        "manual_meetings": [],
        "meeting_preps": [],
        "emails": [],
        "transcripts": [],
        "skipped_transcripts": [],
        "docs": [],
        "skipped": [],
        "threads": [],
        "batches": [],
        "email_manifest": [],
        "definitive_lows": [],
        "pre_skipped": [],
        "companion_files": [],
    }

    # --- Companion file detection pass ---
    # Meeting Recorder produces three files per recording: STEM.txt, STEM.md, STEM.json.
    # Group by stem, classify the .txt first. If it's a MR transcript, mark .md/.json
    # as companions so they aren't misclassified as manual_meetings or docs.
    stems: dict[str, dict[str, Path]] = {}  # stem -> {ext: filepath}
    for filepath in sorted(files):
        stem = filepath.stem
        ext = filepath.suffix.lower()
        stems.setdefault(stem, {})[ext] = filepath

    companion_set: set[str] = set()  # absolute paths of companion files to skip
    for stem, ext_map in stems.items():
        if ".txt" in ext_map:
            # Case A: .txt is the canonical transcript; .md/.json are companions
            txt_path = ext_map[".txt"]
            txt_type, _ = classify_file(txt_path)
            if txt_type != "transcript_mr":
                continue
            for companion_ext in (".md", ".json"):
                if companion_ext in ext_map:
                    comp_path = ext_map[companion_ext]
                    companion_set.add(str(comp_path))
                    result["companion_files"].append({
                        "file": str(comp_path),
                        "companion_of": str(txt_path),
                        "ext": companion_ext,
                    })
                    print(f"Companion: {comp_path.name} → companion of {txt_path.name}", file=sys.stderr)
        elif ".md" in ext_map and ".json" in ext_map:
            # Case B: Meeting Recorder shipped only .md + .json (no .txt). The .md
            # carries frontmatter + timestamped transcript body. If .md classifies
            # as transcript_mr, mark the .json as its companion.
            md_path = ext_map[".md"]
            md_type, _ = classify_file(md_path)
            if md_type != "transcript_mr":
                continue
            json_path = ext_map[".json"]
            companion_set.add(str(json_path))
            result["companion_files"].append({
                "file": str(json_path),
                "companion_of": str(md_path),
                "ext": ".json",
            })
            print(f"Companion: {json_path.name} → companion of {md_path.name}", file=sys.stderr)

    for filepath in sorted(files):
        # Skip companions, they'll be cleaned up with email source files
        if str(filepath) in companion_set:
            continue

        file_type, metadata = classify_file(filepath)

        if file_type == "skip":
            result["skipped"].append(str(filepath))
        elif file_type == "manual_note":
            result["manual_notes"].append(str(filepath))
        elif file_type == "manual_meeting":
            result["manual_meetings"].append(str(filepath))
        elif file_type == "meeting_prep":
            prep_entry = {"file": str(filepath)}
            if metadata and isinstance(metadata, dict):
                prep_entry["has_meeting_content"] = metadata.get("has_meeting_content", False)
            else:
                prep_entry["has_meeting_content"] = False
            result["meeting_preps"].append(prep_entry)
        elif file_type == "email":
            if metadata:
                # Add pre-classification
                relevance, reason = pre_classify(metadata)
                metadata["pre_relevance"] = relevance
                if relevance == "low-definitive":
                    metadata["low_reason"] = reason
                    result["definitive_lows"].append(metadata)
                    print(f"Definitive LOW: {metadata['filename']} - {reason}", file=sys.stderr)
                else:
                    result["email_manifest"].append(metadata)
                    result["emails"].append(str(filepath))
            else:
                # Failed to parse headers, treat as document
                result["docs"].append(str(filepath))
                print(f"Warning: Failed to parse email headers for {filepath}", file=sys.stderr)
        elif file_type == "transcript_mr":
            result["transcripts"].append({
                "file": str(filepath),
                "type": "meeting-recorder",
                **(metadata or {}),
            })
        elif file_type == "transcript_generic":
            result["transcripts"].append({
                "file": str(filepath),
                "type": "generic-transcript",
            })
        elif file_type == "document":
            result["docs"].append(str(filepath))

    # Filter recovered transcripts: skip if primary exists and recovered has zero duration
    if result["transcripts"]:
        primary_subjects = set()
        for t in result["transcripts"]:
            if not t.get("is_recovered", False):
                primary_subjects.add(normalize_subject(t.get("subject", "")).lower())

        kept = []
        for t in result["transcripts"]:
            if t.get("is_recovered", False):
                # Check if primary exists for this meeting
                recovered_subj = normalize_subject(
                    t.get("subject", "").replace("[Recovered] ", "").replace("[Recovered]", "")
                ).lower()
                # Strip hash suffixes from recovered subjects (e.g., "b2b sync_fcdb" → "b2b sync")
                recovered_clean = re.sub(r"_[a-f0-9]{4,}$", "", recovered_subj).strip()
                has_primary = any(recovered_clean in ps for ps in primary_subjects)

                if has_primary or t.get("is_zero_duration", False):
                    result["skipped_transcripts"].append({
                        "file": t["file"],
                        "subject": t.get("subject", ""),
                        "reason": "recovered-duplicate" if has_primary else "zero-duration-recovered",
                    })
                    print(f"Skipped recovered: {Path(t['file']).name}", file=sys.stderr)
                    continue
            kept.append(t)
        result["transcripts"] = kept

    # Cross-source dedup: if both Plaud and Meeting Recorder captured the same meeting,
    # prefer Meeting Recorder (unless it's zero-duration/recovered)
    if result["transcripts"]:
        plaud = [t for t in result["transcripts"] if t.get("plaud_file_id")]
        non_plaud = [t for t in result["transcripts"] if not t.get("plaud_file_id")]
        if plaud and non_plaud:
            from datetime import datetime as _dt, timezone as _tz
            try:
                from zoneinfo import ZoneInfo
                _LOCAL_TZ = ZoneInfo(LOCAL_TZ)
            except ImportError:
                _LOCAL_TZ = None

            def _parse_dt(s: str, is_utc: bool = False):
                """Parse ISO datetime; localize naive values then convert to UTC.

                Plaud's MeetingDate is recording-start in UTC (legacy naive, or
                offset-tagged after a later fix). MR's MeetingDate is the
                calendar event in local time (always naive). Comparing naive
                datetimes across the two sources gave a fixed-offset error
                (the gap between UTC and the local zone) that masked same-meeting pairs.
                """
                if not s:
                    return None
                try:
                    dt = _dt.fromisoformat(s.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    return None
                if dt.tzinfo is None:
                    if is_utc:
                        dt = dt.replace(tzinfo=_tz.utc)
                    elif _LOCAL_TZ is not None:
                        dt = dt.replace(tzinfo=_LOCAL_TZ)
                    else:
                        return dt  # best-effort fallback
                return dt.astimezone(_tz.utc)

            def _duration_seconds(s: str) -> int:
                # "H:MM:SS" or "MM:SS" → seconds
                if not s:
                    return 0
                parts = s.split(":")
                try:
                    parts = [int(p) for p in parts]
                except ValueError:
                    return 0
                if len(parts) == 3:
                    return parts[0] * 3600 + parts[1] * 60 + parts[2]
                if len(parts) == 2:
                    return parts[0] * 60 + parts[1]
                return 0

            def _subjects_match(s1: str, s2: str) -> bool:
                """Check if two meeting subjects refer to the same meeting.
                Normalize both, then check containment or 50%+ shared significant words.
                """
                if not s1 or not s2:
                    return True  # Can't compare, don't block the match
                # Normalize: lowercase, strip reply/forward prefixes
                norm1 = normalize_subject(s1).lower().strip()
                norm2 = normalize_subject(s2).lower().strip()
                if not norm1 or not norm2:
                    return True
                # Containment check
                if norm1 in norm2 or norm2 in norm1:
                    return True
                # Shared significant words (>3 chars), 50% threshold
                words1 = {w for w in norm1.split() if len(w) > 3}
                words2 = {w for w in norm2.split() if len(w) > 3}
                if not words1 or not words2:
                    return True  # Too few significant words to compare
                shared = words1 & words2
                smaller = min(len(words1), len(words2))
                return len(shared) / smaller >= 0.50

            def _is_dup(pt: dict, npt: dict) -> bool:
                """Decide if a Plaud and a Meeting Recorder transcript capture the same meeting.
                Plaud often starts recording well before the calendar event, so start-time
                proximity alone is unreliable. Use a layered heuristic:
                  - Tier 0: calendar subject cross-check (different subjects = different meetings)
                  - Tier 1: start times within 90 min (tightened from 3h to avoid false positives
                    like Plaud 08:04 vs MR 10:05 for two different 1on1s)
                  - Tier 2: recording durations within 20%
                  - Decision: very close start (<30 min) alone is enough, otherwise need both
                    time + duration signals.
                """
                if npt.get("date", "") != pt.get("date", ""):
                    return False

                # Tier 0: calendar subject cross-check. If both have subject info
                # and they don't match, these are different meetings, bail out.
                plaud_subj = pt.get("calendar_subject", "") or ""
                mr_subj = npt.get("subject", "") or ""
                if plaud_subj and mr_subj and not _subjects_match(plaud_subj, mr_subj):
                    return False

                t1 = _parse_dt(pt.get("meeting_datetime", ""), is_utc=True)
                t2 = _parse_dt(npt.get("meeting_datetime", ""), is_utc=False)
                # Tier 1: same date + start times within 90 minutes. Plaud may start
                # 15-30 min early, but not 2 hours early.
                time_close = False
                if t1 and t2:
                    diff = abs((t1 - t2).total_seconds())
                    if diff < 5400:  # 90 minutes
                        time_close = True
                # Tier 2: recording durations within 20% of each other, strong signal
                # that the same conversation was captured by both devices.
                d1 = _duration_seconds(pt.get("recording_duration", ""))
                d2 = _duration_seconds(npt.get("recording_duration", ""))
                duration_close = False
                if d1 >= 60 and d2 >= 60:
                    ratio = min(d1, d2) / max(d1, d2)
                    if ratio >= 0.80:
                        duration_close = True
                # Require either both signals, or very close start (< 30 min) alone.
                if t1 and t2:
                    diff = abs((t1 - t2).total_seconds())
                    if diff < 1800:  # 30 min
                        return True
                return time_close and duration_close

            kept = list(non_plaud)  # always keep non-Plaud
            for pt in plaud:
                is_dup = False
                for npt in non_plaud:
                    if _is_dup(pt, npt):
                        if npt.get("is_zero_duration") or npt.get("is_recovered"):
                            # Plaud wins, skip Meeting Recorder
                            kept = [k for k in kept if k["file"] != npt["file"]]
                            kept.append(pt)
                            result["skipped_transcripts"].append({
                                "file": npt["file"],
                                "subject": npt.get("subject", ""),
                                "reason": "meeting-recorder-broken-plaud-preferred",
                            })
                        else:
                            # Meeting Recorder wins, skip Plaud
                            result["skipped_transcripts"].append({
                                "file": pt["file"],
                                "subject": pt.get("subject", ""),
                                "reason": "plaud-duplicate-of-meeting-recorder",
                            })
                        is_dup = True
                        break
                if not is_dup:
                    kept.append(pt)
            result["transcripts"] = kept

    # Cross-batch dedup: flag transcripts that look like they duplicate an
    # interaction note created by an earlier /w-daily run on the same day
    # (e.g. Plaud arrives hours after Meeting Recorder finished the same call).
    # Conservative: same date + duration within 5% + ≥70% non-Sam attendee
    # overlap of the smaller set. Flags only; does NOT auto-skip. write-notes
    # is the wrong place to throw away content. The flag rides in the manifest
    # so the transcript-processor agent and the briefing layer can react.
    if result["transcripts"]:
        interactions_root = vault / "05-Interactions"

        def _dur_sec(s: str) -> int:
            if not s:
                return 0
            try:
                parts = [int(p) for p in s.split(":")]
            except ValueError:
                return 0
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            if len(parts) == 2:
                return parts[0] * 60 + parts[1]
            return 0

        def _read_meta(path: Path):
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None
            fm_m = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
            if not fm_m:
                return None
            fm = fm_m.group(1)
            d_m = re.search(r"^date:\s*(\S+)", fm, re.MULTILINE)
            dur_m = re.search(r"^recording-duration:\s*['\"]?([\d:]+)['\"]?", fm, re.MULTILINE)
            if not d_m:
                return None
            # Attendees: pull the wikilinks following `attendees:` up to the next top-level key
            att_block = ""
            att_m = re.search(r"^attendees:\s*\n((?:[ \t]+-[^\n]*\n)+)", fm, re.MULTILINE)
            if att_m:
                att_block = att_m.group(1)
            attendees = set(re.findall(r"\[\[([^\]|]+)\]\]", att_block))
            return {
                "date": d_m.group(1).strip(),
                "duration": _dur_sec(dur_m.group(1)) if dur_m else 0,
                "attendees": attendees,
                "path": str(path),
            }

        for t in result["transcripts"]:
            t_date = t.get("date", "")
            if not t_date or len(t_date) < 4:
                continue
            year_dir = interactions_root / t_date[:4]
            if not year_dir.exists():
                continue
            t_dur = _dur_sec(t.get("recording_duration", ""))
            if t_dur < 60:
                continue
            t_attendees = set()
            for att in t.get("resolved_attendees", []):
                wl = (att.get("wikilink", "") or "").strip("[]")
                if wl and wl != OWNER_SLUG:
                    t_attendees.add(wl)
            if not t_attendees:
                continue
            suspected = []
            for note in year_dir.glob(f"{t_date}-*.md"):
                meta = _read_meta(note)
                if not meta or meta["date"] != t_date or meta["duration"] < 60:
                    continue
                ratio = min(meta["duration"], t_dur) / max(meta["duration"], t_dur)
                if ratio < 0.95:
                    continue
                existing_attendees = meta["attendees"] - {OWNER_SLUG}
                if not existing_attendees:
                    continue
                overlap = existing_attendees & t_attendees
                smaller = min(len(existing_attendees), len(t_attendees))
                if smaller == 0 or len(overlap) / smaller < 0.70:
                    continue
                suspected.append(meta["path"])
            if suspected:
                t["suspected_duplicates"] = suspected
                print(f"Suspected dup: {Path(t['file']).name} ~~ {[Path(p).name for p in suspected]}",
                      file=sys.stderr)

    # Clean email bodies if requested
    if args.clean_bodies:
        for email in result["email_manifest"]:
            filepath = Path(email["file"])
            body_start = email.get("body_start_line", 0)
            if body_start > 0:
                clean_email_body(filepath, body_start)
                print(f"Cleaned: {filepath.name}", file=sys.stderr)

        # Load sanitize mappings if PII sanitization requested
        pii_mappings = None
        if args.sanitize_pii:
            pii_mappings = load_sanitize_mappings(vault)
            pii_count_before = len(pii_mappings["emails"]) + len(pii_mappings["phones"])

        # Bundle cleaned bodies into manifest for agent consumption
        for email in result["email_manifest"]:
            filepath = Path(email["file"])
            body_start = email.get("body_start_line", 0)
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                # Body is everything after headers (body_start_line)
                body_text = "".join(lines[body_start:]).strip()
                # Sanitize PII in body before storing in manifest
                if pii_mappings is not None:
                    body_text = sanitize_body_pii(body_text, pii_mappings)
                email["cleaned_body"] = body_text
            except Exception as e:
                print(f"Warning: Could not read cleaned body for {filepath}: {e}",
                      file=sys.stderr)
                email["cleaned_body"] = ""

        # Save updated sanitize mappings if new PII was discovered
        if pii_mappings is not None:
            pii_count_after = len(pii_mappings["emails"]) + len(pii_mappings["phones"])
            new_pii = pii_count_after - pii_count_before
            if new_pii > 0:
                save_sanitize_mappings(vault, pii_mappings)
                print(f"PII sanitized: {new_pii} new tokens added "
                      f"({len(pii_mappings['emails'])} emails, "
                      f"{len(pii_mappings['phones'])} phones total)",
                      file=sys.stderr)
            else:
                print(f"PII sanitized: all tokens already known", file=sys.stderr)

    # Entity resolution, duplicate detection, and frontmatter generation
    if args.resolve_entities:
        lookup = load_email_lookup(vault)
        ingest_log = load_ingest_log(vault)
        resolved_count = 0
        dup_count = 0

        # Resolve emails and check duplicates
        kept_emails = []
        kept_manifest = []
        for email in result["email_manifest"]:
            # Entity resolution first: the content-dedup branch of
            # check_already_processed compares RESOLVED recipient wikilinks
            # (resolved_to/resolved_cc) against the candidate note's frontmatter,
            # so recipients must be resolved before the check (cheap: dict lookups,
            # no extra I/O). The cheap by_source filename check inside it is
            # unaffected by ordering.
            resolve_participants(email, lookup)

            # Duplicate check
            skip_reason = check_already_processed(email, ingest_log, vault)
            if skip_reason:
                dup_count += 1
                email["skip_reason"] = skip_reason
                result.setdefault("pre_skipped", []).append({
                    "file": email["file"],
                    "filename": email["filename"],
                    "reason": skip_reason,
                    "subject": email.get("subject", ""),
                    "date": email.get("date", ""),
                })
                # Remove from emails list too
                continue

            resolved_count += 1

            # Frontmatter
            email["frontmatter"] = generate_email_frontmatter(email)
            email["output_filename"] = generate_email_filename(email)

            kept_emails.append(email["file"])
            kept_manifest.append(email)

        result["emails"] = kept_emails
        result["email_manifest"] = kept_manifest

        # Resolve transcript attendees
        for transcript in result["transcripts"]:
            resolve_transcript_attendees(transcript, lookup)
            fm = generate_transcript_frontmatter(transcript)
            transcript["frontmatter"] = fm
            # Propagate any 1on1 auto-flip (done inside generate_transcript_frontmatter
            # when attendee count == 2 + Sam) back to the top-level field so the
            # compact summary matches the frontmatter and output_filename.
            transcript["meeting_type"] = fm.get("meeting-type", transcript.get("meeting_type", "general"))
            transcript["output_filename"] = generate_transcript_filename(transcript, fm)

        # Resolve definitive LOW entities too (for registry-only additions)
        for low in result["definitive_lows"]:
            resolve_participants(low, lookup)

        print(f"Resolved entities for {resolved_count} emails, "
              f"{len(result['transcripts'])} transcripts. "
              f"Skipped {dup_count} duplicates.", file=sys.stderr)

    # Load thread index if provided
    thread_index = None
    if args.thread_index:
        try:
            with open(args.thread_index, "r", encoding="utf-8") as f:
                thread_index = json.load(f)
            print(f"Loaded thread index: {len(thread_index.get('by_conversation_id', {}))} conv-ids, "
                  f"{len(thread_index.get('by_normalized_subject', {}))} subjects", file=sys.stderr)
        except Exception as e:
            print(f"Warning: Could not load thread index: {e}", file=sys.stderr)

    # Group emails into threads
    if result["email_manifest"]:
        result["threads"] = group_threads(result["email_manifest"])

        # Enrich threads with existing notes from thread index
        if thread_index:
            by_conv = thread_index.get("by_conversation_id", {})
            by_subj = thread_index.get("by_normalized_subject", {})

            for thread in result["threads"]:
                existing = []
                conv_id = thread.get("conversation_id")

                # Look up by ConversationId first
                if conv_id and conv_id in by_conv:
                    existing.extend(by_conv[conv_id])

                # Fall back to normalized subject (with 14-day date window)
                if not existing:
                    for email in thread["emails"]:
                        norm_subj = email.get("normalized_subject", "")
                        if norm_subj and norm_subj in by_subj:
                            email_date = email.get("date", "")
                            for entry in by_subj[norm_subj]:
                                # Only match if existing note is within 14 days
                                entry_date = entry.get("date", "")
                                if email_date and entry_date:
                                    try:
                                        d1 = datetime.strptime(email_date[:10], "%Y-%m-%d")
                                        d2 = datetime.strptime(entry_date[:10], "%Y-%m-%d")
                                        if abs((d1 - d2).days) <= 14:
                                            existing.append(entry)
                                    except ValueError:
                                        existing.append(entry)  # can't parse date, include anyway
                                else:
                                    existing.append(entry)
                            break  # All emails in thread share the subject

                if existing:
                    # Deduplicate by path
                    seen = set()
                    unique = []
                    for e in existing:
                        if e["path"] not in seen:
                            seen.add(e["path"])
                            unique.append(e)
                    thread["existing_thread_notes"] = unique

        # Plan batches
        result["batches"] = plan_batches(result["threads"])

    # Summary stats to stderr
    counts = {
        "emails": len(result["emails"]),
        "definitive_lows": len(result["definitive_lows"]),
        "transcripts": len(result["transcripts"]),
        "skipped_transcripts": len(result["skipped_transcripts"]),
        "companion_files": len(result["companion_files"]),
        "docs": len(result["docs"]),
        "manual_notes": len(result["manual_notes"]),
        "manual_meetings": len(result["manual_meetings"]),
        "meeting_preps": len(result["meeting_preps"]),
        "skipped": len(result["skipped"]),
    }
    total = sum(v for k, v in counts.items() if k not in ("skipped", "skipped_transcripts", "definitive_lows"))
    total += len(result["definitive_lows"])  # Count definitive lows in total (they're real files)
    print(f"Classified {total} files: {counts}", file=sys.stderr)

    # Backlog detection: either >20 emails OR the processable content spans >4 days.
    # The second clause catches vacation-return cases where file count is modest
    # but the date range is wide, they still benefit from sequential batching.
    def _collect_dates(items):
        out = []
        for it in items:
            d = it.get("date") or ""
            if d:
                out.append(d)
        return out

    all_dates = _collect_dates(result["email_manifest"]) + _collect_dates(result["transcripts"])
    date_span_days = 0
    if len(all_dates) >= 2:
        try:
            from datetime import date as _date
            parsed = [_date.fromisoformat(d) for d in all_dates if len(d) == 10]
            if parsed:
                date_span_days = (max(parsed) - min(parsed)).days
        except ValueError:
            pass

    is_backlog = counts["emails"] > 20 or date_span_days > 4
    backlog_reason = None
    if is_backlog:
        if counts["emails"] > 20:
            backlog_reason = f"email-count>{20} ({counts['emails']})"
        else:
            backlog_reason = f"date-span>{4}d ({date_span_days}d)"
        print(f"Backlog mode: {backlog_reason}", file=sys.stderr)

    # Remove non-serializable data and body_preview for cleaner output
    for email in result["email_manifest"]:
        email.pop("body_preview", None)
        email.pop("body_scan", None)
        email.pop("body_start_line", None)
    for email in result["definitive_lows"]:
        email.pop("body_preview", None)
        email.pop("body_scan", None)
        email.pop("body_start_line", None)

    # Stash backlog info in the full manifest too
    result["is_backlog"] = is_backlog
    result["backlog_reason"] = backlog_reason
    result["date_span_days"] = date_span_days

    # Run-time estimate for the /w-daily heads-up and lite mode. Also stamps
    # each transcript with its stakes class + per-item estimate, which the
    # compact transcript list below surfaces for the lite-mode choice prompt.
    eta = estimate_eta(result, counts)
    result["eta"] = eta

    # Always write full manifest to file, compact summary to stdout
    manifest_path = args.manifest_file or os.path.join(vault, "_db", "manifest.json")
    atomic_json_write(Path(manifest_path), result)
    print(f"Manifest written to {manifest_path}", file=sys.stderr)

    # Compact summary for master context
    summary = {
        "manifest_file": manifest_path,
        "counts": counts,
        "total": total,
        "is_backlog": is_backlog,
        "backlog_reason": backlog_reason,
        "date_span_days": date_span_days,
        "eta": eta,
        "batches": [
            {"batch_index": i, "file_count": len(b["files"]), "thread_count": len(b["thread_groups"])}
            for i, b in enumerate(result["batches"])
        ],
        # Compact email list: just file, subject, date, pre_relevance, vip, output_filename
        "emails": [
            {
                "file": e["file"],
                "subject": e["subject"],
                "date": e["date"],
                "pre_relevance": e.get("pre_relevance", "medium"),
                "direction": e.get("direction"),
                "vip_involved": e.get("vip_involved", []),
                "output_filename": e.get("output_filename", ""),
                "thread_id": e.get("conversation_id", ""),
            }
            for e in result["email_manifest"]
        ],
        # Compact transcript list: file, subject, date, meeting_type, stakes,
        # est_minutes, output_filename (stakes/est_minutes drive lite-mode choice)
        "transcripts": [
            {
                "file": t["file"],
                "subject": t.get("subject", ""),
                "date": t.get("date", ""),
                "meeting_type": t.get("meeting_type", "general"),
                "stakes": t.get("stakes", "substantive"),
                "est_minutes": t.get("est_minutes", ETA_TRANSCRIPT_SUBSTANTIVE),
                "output_filename": t.get("output_filename", ""),
            }
            for t in result["transcripts"]
        ],
        # Definitive LOWs: compact for logging
        "definitive_lows": [
            {
                "file": d["file"],
                "subject": d["subject"],
                "date": d["date"],
                "low_reason": d.get("low_reason", ""),
            }
            for d in result["definitive_lows"]
        ],
        "pre_skipped": result["pre_skipped"],
        "skipped_transcripts": result["skipped_transcripts"],
        "companion_files": result["companion_files"],
        "manual_notes": result["manual_notes"],
        "manual_meetings": result["manual_meetings"],
        "meeting_preps": result["meeting_preps"],
        "docs": result["docs"],
        "skipped": result["skipped"],
        # Thread summary: count + which emails share threads
        "thread_count": len(result["threads"]),
        "threads_with_existing": [
            {
                "normalized_subject": t["emails"][0]["normalized_subject"] if t["emails"] else "",
                "email_count": len(t["emails"]),
                "existing_notes": len(t.get("existing_thread_notes", [])),
            }
            for t in result["threads"]
            if len(t["emails"]) > 1 or t.get("existing_thread_notes")
        ],
    }
    json.dump(summary, sys.stdout, indent=2, ensure_ascii=False)
    print()  # trailing newline


if __name__ == "__main__":
    main()
