#!/usr/bin/env python3
"""Enrich Plaud transcripts with calendar metadata.

Matches Plaud recordings in 00-Inbox/ to calendar events by time overlap,
then rewrites transcript headers with calendar attendees and metadata.

Runs after pull-plaud.py and archive-calendar.py, before classify-inbox.py.

Usage:
  python enrich-plaud-transcripts.py --vault /path/to/vault
  python enrich-plaud-transcripts.py --vault /path/to/vault --quiet
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from utils import LOCAL_TZ as _LOCAL_TZ_NAME

# Calendar events from Power Automate are in local time. Configure the zone in utils.py.
LOCAL_TZ = ZoneInfo(_LOCAL_TZ_NAME)


def parse_transcript_headers(filepath):
    """Parse Meeting Recorder-style headers from a transcript file.

    Returns (headers_dict, header_end_line_index, full_lines).
    """
    lines = filepath.read_text(encoding="utf-8").splitlines(keepends=True)
    headers = {}
    header_end = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            header_end = i
            break
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            headers[key.strip()] = value.strip()

    return headers, header_end, lines


def parse_iso_datetime(s):
    """Parse ISO datetime string to datetime object (UTC)."""
    try:
        # Handle various ISO formats
        s = s.replace("Z", "+00:00")
        if "T" in s:
            # Remove fractional seconds beyond 6 digits
            s = re.sub(r"(\.\d{6})\d+", r"\1", s)
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    except (ValueError, TypeError):
        pass
    return None


def parse_duration_to_seconds(duration_str):
    """Parse H:MM:SS to total seconds."""
    parts = duration_str.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return 0


def time_overlap(rec_start, rec_end, ev_start, ev_end, margin_minutes=15):
    """Check if recording overlaps with event (with margin)."""
    margin = timedelta(minutes=margin_minutes)
    # Recording overlaps if it starts before event ends + margin
    # and ends after event starts - margin
    return rec_start < (ev_end + margin) and rec_end > (ev_start - margin)


def overlap_score(rec_start, rec_end, ev_start, ev_end):
    """Score how well a recording matches an event by time. 0.0 to 1.0."""
    overlap_start = max(rec_start, ev_start)
    overlap_end = min(rec_end, ev_end)
    if overlap_start >= overlap_end:
        return 0.0
    overlap_secs = (overlap_end - overlap_start).total_seconds()
    rec_secs = max((rec_end - rec_start).total_seconds(), 1)
    return overlap_secs / rec_secs


def subject_similarity(plaud_subject, cal_subject):
    """Simple word-overlap similarity between Plaud and calendar subjects.

    Returns 0.0 to 1.0. Higher means more words in common.
    """
    def normalize(s):
        # Strip date prefixes like "04-13 ", lowercase, split words
        s = re.sub(r"^\d{2}-\d{2}\s+", "", s)
        s = re.sub(r"[^\w\s]", " ", s.lower())
        return set(s.split()) - {"the", "and", "a", "an", "of", "in", "for", "to", "with", "on"}

    words_p = normalize(plaud_subject)
    words_c = normalize(cal_subject)
    if not words_p or not words_c:
        return 0.0
    common = words_p & words_c
    return len(common) / max(len(words_p), len(words_c))


def parse_calendar_datetime(s):
    """Parse calendar datetime as local time (LOCAL_TZ) and convert to UTC.

    Power Automate calendar events have no timezone info but are local time.
    """
    dt = parse_iso_datetime(s)
    if dt is None:
        return None
    # If naive (no tz), treat as LOCAL_TZ time
    if dt.tzinfo is None or dt.tzinfo == timezone.utc:
        # parse_iso_datetime defaults to UTC for naive, but calendar is local
        naive = dt.replace(tzinfo=None)
        dt = naive.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    return dt


def match_to_calendar(headers, events):
    """Find the best calendar event match for a Plaud transcript.

    Plaud timestamps are UTC. Calendar timestamps are local (LOCAL_TZ).
    Returns matched event dict or None.
    """
    meeting_date_str = headers.get("MeetingDate", "")
    duration_str = headers.get("RecordingDuration", "0:00:00")

    rec_start = parse_iso_datetime(meeting_date_str)
    if not rec_start:
        return None

    duration_secs = parse_duration_to_seconds(duration_str)
    rec_end = rec_start + timedelta(seconds=duration_secs)

    plaud_subject = headers.get("MeetingSubject", "")

    candidates = []
    for event in events:
        # Skip all-day events
        if event.get("isAllDay", False):
            continue

        ev_start = parse_calendar_datetime(event.get("start", ""))
        ev_end = parse_calendar_datetime(event.get("end", ""))
        if not ev_start or not ev_end:
            continue

        if time_overlap(rec_start, rec_end, ev_start, ev_end):
            time_score = overlap_score(rec_start, rec_end, ev_start, ev_end)
            subj_score = subject_similarity(plaud_subject, event.get("subject", ""))
            # Combined score: subject match is weighted higher since user names recordings
            combined = time_score * 0.4 + subj_score * 0.6
            candidates.append((combined, time_score, subj_score, event))

    if not candidates:
        return None

    # Best match by combined score
    candidates.sort(key=lambda x: x[0], reverse=True)
    best_combined, best_time, best_subj, best_event = candidates[0]

    # Attendee-count sanity guard: recordings are typically the owner's 2-15
    # person working meetings. A match against a 50+-attendee calendar event with
    # no subject overlap is almost certainly wrong (e.g. a small 1on1 matched to a
    # large all-hands or training that happened to run in the same time window).
    event_attendees = best_event.get("attendees", "") or ""
    attendee_count = len([a for a in event_attendees.split(";") if a.strip()])
    if best_subj < 0.1 and attendee_count > 15:
        return None  # huge invite + no subject match = wrong meeting

    # Confidence threshold:
    # - Strong subject match (>= 0.2): trust it even with partial time overlap
    # - No subject match: require strong time overlap (>= 0.5) to avoid false matches
    # - Marginal time overlap + no subject = almost certainly wrong meeting
    if best_subj >= 0.2:
        return best_event  # subject match is strong signal
    if best_time < 0.5:
        return None  # not enough confidence without subject match

    return best_event


def rewrite_transcript(filepath, headers, header_end, lines, matched_event):
    """Rewrite transcript file headers with calendar data."""
    # Build new attendees from calendar
    cal_attendees = matched_event.get("attendees", "")
    cal_optional = matched_event.get("optionalAttendees", "")

    # Combine attendees, dedup
    all_attendees = set()
    for field in [cal_attendees, cal_optional]:
        for addr in field.split(";"):
            addr = addr.strip()
            if addr:
                all_attendees.add(addr)

    # Build new header block
    new_headers = []
    new_headers.append(f"MeetingSubject: {headers.get('MeetingSubject', 'Untitled')}\n")
    new_headers.append(f"MeetingDate: {headers.get('MeetingDate', '')}\n")
    new_headers.append(f"Attendees: {'; '.join(sorted(all_attendees))}\n")
    new_headers.append(f"MeetingType: {headers.get('MeetingType', 'general')}\n")
    new_headers.append(f"RecordingDuration: {headers.get('RecordingDuration', '0:00:00')}\n")

    # Preserve PlaudFileId
    if "PlaudFileId" in headers:
        new_headers.append(f"PlaudFileId: {headers['PlaudFileId']}\n")

    # Add calendar metadata
    new_headers.append(f"CalendarMatch: true\n")
    new_headers.append(f"CalendarSubject: {matched_event.get('subject', '')}\n")
    organizer = matched_event.get("organizer", "")
    if organizer:
        new_headers.append(f"CalendarOrganizer: {organizer}\n")

    # Reconstruct file: new headers + blank line + body
    new_content = "".join(new_headers) + "\n" + "".join(lines[header_end + 1:])
    filepath.write_text(new_content, encoding="utf-8")


def add_no_match_header(filepath, headers, header_end, lines):
    """Add CalendarMatch: false to transcript that didn't match."""
    # Check if CalendarMatch already present
    if "CalendarMatch" in headers:
        return

    # Insert CalendarMatch: false before the blank line
    insert_line = f"CalendarMatch: false\n"
    lines.insert(header_end, insert_line)
    filepath.write_text("".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Enrich Plaud transcripts with calendar data")
    parser.add_argument("--vault", required=True, help="Path to vault root")
    parser.add_argument("--quiet", action="store_true", help="Suppress output")
    args = parser.parse_args()

    vault = Path(args.vault)
    inbox = vault / "00-Inbox"
    history_path = vault / "_db" / "calendar-history.json"

    def log(msg):
        if not args.quiet:
            print(msg)

    # Find Plaud transcripts in inbox
    plaud_files = sorted(inbox.glob("transcript-plaud-*.txt"))
    if not plaud_files:
        log("No Plaud transcripts to enrich")
        return

    # Load calendar history
    if not history_path.exists():
        log("No calendar history found, skipping enrichment")
        # Still add CalendarMatch: false to all
        for pf in plaud_files:
            headers, header_end, lines = parse_transcript_headers(pf)
            add_no_match_header(pf, headers, header_end, lines)
        return

    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log("Failed to read calendar history, skipping enrichment")
        return

    events = history.get("events", [])
    if not events:
        log("Calendar history is empty, skipping enrichment")
        return

    matched = 0
    unmatched = 0

    for pf in plaud_files:
        headers, header_end, lines = parse_transcript_headers(pf)

        # Skip if already enriched
        if "CalendarMatch" in headers:
            continue

        event = match_to_calendar(headers, events)

        if event:
            rewrite_transcript(pf, headers, header_end, lines, event)
            log(f"  Matched: {pf.name} -> {event.get('subject', '?')}")
            matched += 1
        else:
            add_no_match_header(pf, headers, header_end, lines)
            unmatched += 1

    log(f"Enrichment: {matched} matched, {unmatched} unmatched")


if __name__ == "__main__":
    main()
