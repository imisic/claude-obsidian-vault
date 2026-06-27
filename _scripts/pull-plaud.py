#!/usr/bin/env python3
"""Pull new recordings from Plaud and convert to Meeting Recorder transcript format.

Authenticates via plaud_api.load_plaud_auth() (OAuth from `plaud login`, with the
legacy .env token as fallback), fetches recordings newer than the last sync, and
drops transcripts into 00-Inbox/ in the format classify-inbox.py understands.

Auth setup (one-time, per machine):
  1. npm install -g @plaud-ai/cli
  2. plaud login        # browser OAuth -> ~/.plaud/tokens.json (auto-refreshed)
Legacy fallback (being retired): a web.plaud.ai `tokenstr` in _scripts/.env as
PLAUD_TOKEN; used only when OAuth isn't configured.

Usage:
  python pull-plaud.py                  # pull new recordings since last sync
  python pull-plaud.py --all            # pull all recordings (first run)
  python pull-plaud.py --download-audio # also save MP3s to _attachments/
  python pull-plaud.py --dry-run        # show what would be pulled, don't write
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)

from plaud_api import load_plaud_auth, PlaudClient

# Paths
SCRIPT_DIR = Path(__file__).parent
VAULT_ROOT = SCRIPT_DIR.parent
INBOX_DIR = VAULT_ROOT / "00-Inbox"
ATTACHMENTS_DIR = VAULT_ROOT / "_attachments"
DB_DIR = VAULT_ROOT / "_db"
SYNC_STATE_FILE = DB_DIR / "plaud-sync.json"
SPEAKER_MAP_FILE = DB_DIR / "plaud-speaker-map.json"


def load_sync_state():
    """Load last sync timestamp. Returns epoch millis or 0."""
    if SYNC_STATE_FILE.exists():
        data = json.loads(SYNC_STATE_FILE.read_text(encoding="utf-8"))
        return data.get("last_sync_epoch_ms", 0)
    return 0


def save_sync_state(epoch_ms):
    """Save last sync timestamp."""
    data = {
        "last_sync_epoch_ms": epoch_ms,
        "last_sync_iso": datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat(),
        "updated": datetime.now(tz=timezone.utc).isoformat(),
    }
    SYNC_STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def ms_to_timestamp(ms):
    """Convert milliseconds to [H:MM:SS] format."""
    total_seconds = int(ms / 1000)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"[{hours}:{minutes:02d}:{seconds:02d}]"


def load_email_lookup():
    """Load email-lookup.json for speaker resolution. Returns dict or empty."""
    lookup_path = DB_DIR / "email-lookup.json"
    if not lookup_path.exists():
        return {}
    try:
        return json.loads(lookup_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_name_to_email_map(lookup):
    """Build firstname.lastname -> email map from email-lookup.json.

    Used to resolve Plaud speakers that don't have @ in their name.
    """
    name_map = {}  # lowercase "firstname.lastname" -> email
    for email in lookup:
        local = email.split("@")[0].lower()
        if "." in local:
            if local in name_map:
                name_map[local] = None  # ambiguous, multiple emails for same name pattern
            else:
                name_map[local] = email
    return name_map


def resolve_plaud_speaker(speaker_raw, lookup, name_map):
    """Resolve a Plaud speaker name to an email address.

    Handles three cases:
    1. Has @ + exact match in lookup: return as-is
    2. Has @ + no match (truncated): prefix-match against lookup keys
    3. No @ (firstname.lastname): match against name_map

    Returns (resolved_email_or_original, was_resolved).
    """
    if not speaker_raw or speaker_raw.startswith("Speaker "):
        return speaker_raw, False

    speaker_lower = speaker_raw.lower().strip()

    if "@" in speaker_lower:
        # Case 1: exact match
        if speaker_lower in lookup:
            return speaker_lower, True
        # Case 2: truncated email - prefix match
        matches = [e for e in lookup if e.startswith(speaker_lower)]
        if len(matches) == 1:
            return matches[0], True
        # Still unresolved, return as-is
        return speaker_raw, False

    # Case 3: firstname.lastname (no @)
    if "." in speaker_lower:
        email = name_map.get(speaker_lower)
        if email:  # None means ambiguous
            return email, True

    return speaker_raw, False


def plaud_speaker_to_label(speaker):
    """Convert Plaud speaker name to Meeting Recorder label for transcript body.

    Handles: emails, firstname.lastname, Speaker N.
    """
    if not speaker or speaker.startswith("Speaker "):
        return speaker or "Unknown"
    # Email format: extract name from local part
    if "@" in speaker:
        local = speaker.split("@")[0]
        parts = local.split(".")
        if len(parts) >= 2:
            return "-".join(p.capitalize() for p in parts[:2])
        return parts[0].capitalize()
    # firstname.lastname format
    parts = speaker.split(".")
    if len(parts) >= 2:
        return "-".join(p.capitalize() for p in parts)
    return speaker.capitalize()


def _fetch_s3_content(url, expect_json=True):
    """Fetch content from a Plaud S3 pre-signed URL.

    Validates hostname, handles gzip transparently.
    Returns parsed JSON (if expect_json) or text.
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if not (hostname.endswith(".amazonaws.com") or hostname.endswith(".plaud.ai")):
        raise ValueError(f"Untrusted host: {hostname}")

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    # Handle optional gzip
    import gzip
    try:
        raw = gzip.decompress(resp.content)
    except (gzip.BadGzipFile, OSError):
        raw = resp.content

    if expect_json:
        return json.loads(raw)
    return raw.decode("utf-8", errors="replace")


def fetch_transcript_segments(detail):
    """Fetch transcript segments. Each segment is shaped:
    {start_time, end_time, content, speaker, original_speaker}

    oauth: inline JSON string in source_list[type=transaction].data_content.
    legacy: JSON array on S3, referenced by content_list[type=transaction].data_link.
    """
    items = detail.get("source_list") or detail.get("content_list") or []
    trans_items = [c for c in items if c.get("data_type") == "transaction"]
    if not trans_items:
        return None, "no_transcript"

    inline = trans_items[0].get("data_content")
    if inline:
        try:
            segments = json.loads(inline)
        except (ValueError, TypeError):
            return None, "bad_inline_transcript"
    else:
        trans_url = trans_items[0].get("data_link")
        if not trans_url:
            return None, "no_transcript_url"
        try:
            segments = _fetch_s3_content(trans_url, expect_json=True)
        except (ValueError, requests.HTTPError) as e:
            return None, f"transcript_fetch_error:{e}"

    if not isinstance(segments, list) or len(segments) == 0:
        return None, "empty_transcript"

    return segments, "ok"


def fetch_plaud_ai_content(detail):
    """Fetch Plaud's AI-generated content (summary, minutes, outline).

    oauth: inline in note_list[].data_content. legacy: S3 via content_list[].data_link.
    Returns dict with available content types.
    """
    items = detail.get("note_list")
    if items is None:
        items = [c for c in detail.get("content_list", []) if c.get("data_type") != "transaction"]

    result = {}
    for item in items:
        dtype = item.get("data_type", "")
        if dtype == "transaction":
            continue
        inline = item.get("data_content")
        url = item.get("data_link", "")
        try:
            if dtype == "outline":
                result["outline"] = json.loads(inline) if inline else (
                    _fetch_s3_content(url, expect_json=True) if url else None)
            elif dtype in ("auto_sum_note", "consumer_note"):
                label = item.get("data_tab_name") or dtype
                result[label] = inline if inline else (
                    _fetch_s3_content(url, expect_json=False) if url else None)
        except Exception:
            pass  # non-critical, transcript is what matters

    return {k: v for k, v in result.items() if v}


def load_speaker_map():
    """Load _db/plaud-speaker-map.json: curated map of Plaud speaker labels
    (email or name, lowercased) -> vault person slug (FirstName-LastName).
    Checked before email-lookup. Keys starting with '_' are ignored (for docs)."""
    if not SPEAKER_MAP_FILE.exists():
        return {}
    try:
        raw = json.loads(SPEAKER_MAP_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        k.strip().lower(): v.strip()
        for k, v in raw.items()
        if not k.startswith("_") and isinstance(v, str) and v.strip()
    }


def _resolve_speaker(speaker_raw, lookup, name_map, speaker_map):
    """Resolve one Plaud speaker to (attendee_value, body_label, resolved).

    Priority: curated speaker_map -> email/name lookup -> raw string.
    Generic 'Speaker N' / 'Unknown' are never attendees and count as unresolved.
    """
    raw = (speaker_raw or "Unknown").strip()

    # 1. Curated override (email or name -> vault slug), used for attendee + label
    if speaker_map:
        slug = speaker_map.get(raw.lower())
        if slug:
            return slug, slug, True

    # 2. Generic diarization labels: not attendees, surfaced as unresolved
    if not raw or raw == "Unknown" or raw.startswith("Speaker "):
        return None, raw or "Unknown", False

    # 3. Email / firstname.lastname -> vault email
    label = plaud_speaker_to_label(raw)
    if lookup and name_map:
        resolved, ok = resolve_plaud_speaker(raw, lookup, name_map)
        if ok:
            return resolved, label, True

    # Unresolved name/email: keep as attendee (likely external) but flag it
    return raw, label, False


def extract_transcript_lines(detail, lookup=None, name_map=None, speaker_map=None):
    """Extract transcript into timestamped speaker lines.

    Resolves each speaker via the curated speaker_map, then email-lookup, for both
    the attendee header and the body label.
    Returns (lines, attendees, unresolved_raw, status).
    """
    segments, status = fetch_transcript_segments(detail)
    if segments is None:
        return None, set(), set(), status

    lines = []
    attendees = set()
    unresolved = set()
    cache = {}

    for seg in segments:
        text = (seg.get("content") or "").strip()
        if not text:
            continue
        speaker_raw = seg.get("speaker", "Unknown")
        if speaker_raw not in cache:
            cache[speaker_raw] = _resolve_speaker(speaker_raw, lookup, name_map, speaker_map)
        attendee, label, ok = cache[speaker_raw]
        if attendee:
            attendees.add(attendee)
        if not ok:
            unresolved.add(speaker_raw)
        lines.append(f"{ms_to_timestamp(seg.get('start_time', 0))} {label}: {text}")

    if not lines:
        return None, set(), set(), "empty_transcript"

    return lines, attendees, unresolved, status


def format_duration(duration_ms):
    """Convert duration in milliseconds to H:MM:SS."""
    total_seconds = int(duration_ms / 1000)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours}:{minutes:02d}:{seconds:02d}"


def sanitize_filename(name, max_len=60):
    """Make a filesystem-safe name."""
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"\s+", "-", name.strip())
    name = re.sub(r"-{2,}", "-", name).strip("-").lower()
    return name[:max_len] if name else "untitled"


def speakers_to_attendees(speakers):
    """Convert resolved speaker set to semicolon-separated attendee list.

    Speakers may be emails, firstname.lastname, or 'Speaker N'.
    Generic 'Speaker N' are filtered out.
    """
    attendees = []
    for s in sorted(speakers):
        if s.startswith("Speaker ") or s == "Unknown":
            continue
        attendees.append(s)
    return "; ".join(attendees) if attendees else "plaud-import"


def build_transcript_file(detail, file_summary, include_ai=False, lookup=None, name_map=None, speaker_map=None):
    """Build a Meeting Recorder-compatible transcript string from Plaud data.

    Returns (content, status, unresolved_speakers).
    """
    file_id = file_summary.get("id", "unknown")
    filename = detail.get("file_name") or detail.get("name") or file_summary.get("filename") or "Untitled"
    duration_ms = file_summary.get("duration", 0)
    start_time = file_summary.get("start_time", 0)

    # Parse date
    if isinstance(start_time, (int, float)) and start_time > 1000000000000:
        dt = datetime.fromtimestamp(start_time / 1000, tz=timezone.utc)
    elif isinstance(start_time, str):
        dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    else:
        dt = datetime.now(tz=timezone.utc)

    date_iso = dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    if date_iso.endswith("+0000"):
        date_iso = date_iso[:-5] + "+00:00"

    # Extract transcript lines (speaker resolution: curated map -> email-lookup)
    transcript_lines, speakers, unresolved, status = extract_transcript_lines(
        detail, lookup, name_map, speaker_map)
    if transcript_lines is None:
        return None, status, set()

    attendees = speakers_to_attendees(speakers)

    header = (
        f"MeetingSubject: {filename}\n"
        f"MeetingDate: {date_iso}\n"
        f"Attendees: {attendees}\n"
        f"MeetingType: general\n"
        f"RecordingDuration: {format_duration(duration_ms)}\n"
        f"PlaudFileId: {file_id}\n"
    )

    body = "\n".join(transcript_lines)
    content = f"{header}\n{body}\n"

    # Optionally append Plaud's AI content as a reference block
    if include_ai:
        ai_content = fetch_plaud_ai_content(detail)
        if ai_content:
            content += "\n--- PLAUD AI CONTENT (reference only) ---\n"
            for label, text in ai_content.items():
                if label == "outline":
                    # Format outline as readable topic list
                    content += f"\n### Outline\n"
                    for topic in text if isinstance(text, list) else []:
                        ts = ms_to_timestamp(topic.get("start_time", 0))
                        content += f"  {ts} {topic.get('topic', '')}\n"
                elif isinstance(text, str):
                    content += f"\n### {label}\n{text}\n"

    return content, "ok", unresolved


def main():
    parser = argparse.ArgumentParser(description="Pull Plaud recordings into Vault inbox")
    parser.add_argument("--all", action="store_true", help="Pull all recordings, not just new ones")
    parser.add_argument("--download-audio", action="store_true", help="Also download MP3 to _attachments/")
    parser.add_argument("--include-ai", action="store_true", help="Append Plaud AI summary/minutes to transcript")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be pulled, don't write files")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output (for pipeline use)")
    parser.add_argument("--limit", type=int, default=0, help="Max number of recordings to pull (0 = all new)")
    args = parser.parse_args()

    def log(msg):
        if not args.quiet:
            print(msg)

    auth = load_plaud_auth()
    if auth is None:
        print("Plaud: no auth configured, skipping", file=sys.stderr)
        return

    client = PlaudClient(auth)

    # Load speaker resolution data: email lookup + curated override map
    lookup = load_email_lookup()
    name_map = load_name_to_email_map(lookup) if lookup else {}
    speaker_map = load_speaker_map()

    last_sync = 0 if args.all else load_sync_state()

    # Fetch file list
    log(f"Fetching file list from Plaud [{auth.mode}] ({auth.base})...")
    all_files = client.list_all_files()
    log(f"Found {len(all_files)} total recordings")

    # Filter to new files (by start_time > last_sync), skip trashed
    new_files = [
        f for f in all_files
        if not f.get("is_trash", False)
        and f.get("is_trans", False)  # only files with transcripts
        and f.get("start_time", 0) > last_sync
    ]
    new_files.sort(key=lambda f: f.get("start_time", 0))

    if args.limit and args.limit > 0:
        new_files = new_files[:args.limit]

    if not new_files:
        log("No new recordings to pull.")
        return

    log(f"{len(new_files)} new recording(s) to pull{' (dry run)' if args.dry_run else ''}:")
    for f in new_files:
        ts = datetime.fromtimestamp(f["start_time"] / 1000, tz=timezone.utc)
        dur = format_duration(f.get("duration", 0))
        log(f"  - {ts.strftime('%Y-%m-%d %H:%M')} | {dur} | {f.get('filename', '?')}")

    if args.dry_run:
        return

    # Process each recording
    max_epoch = last_sync
    pulled = 0
    skipped = 0
    errors = 0
    unresolved_speakers = {}

    for f in new_files:
        file_id = f["id"]
        filename = f.get("filename", "untitled")
        start_time = f.get("start_time", 0)

        try:
            log(f"  Fetching detail: {filename}...")
            detail = client.get_file_detail(file_id)

            transcript_content, status, unresolved = build_transcript_file(
                detail, f, include_ai=args.include_ai,
                lookup=lookup, name_map=name_map, speaker_map=speaker_map
            )

            if transcript_content is None:
                log(f"    Skipped ({status})")
                skipped += 1
                continue

            # Write transcript to inbox
            dt = datetime.fromtimestamp(start_time / 1000, tz=timezone.utc)
            date_str = dt.strftime("%Y-%m-%d")
            slug = sanitize_filename(filename, max_len=50)
            out_name = f"transcript-plaud-{date_str}-{slug}.txt"
            out_path = INBOX_DIR / out_name

            # Collision handling
            counter = 2
            while out_path.exists():
                out_name = f"transcript-plaud-{date_str}-{slug}-{counter}.txt"
                out_path = INBOX_DIR / out_name
                counter += 1

            out_path.write_text(transcript_content, encoding="utf-8")
            log(f"    -> {out_name}")
            pulled += 1
            for spk in unresolved:
                unresolved_speakers[spk] = unresolved_speakers.get(spk, 0) + 1

            # Optional audio download
            if args.download_audio:
                audio_name = f"{date_str}-plaud-{slug}.mp3"
                audio_path = ATTACHMENTS_DIR / audio_name
                if not audio_path.exists():
                    log(f"    Downloading audio -> {audio_name}...")
                    client.download_audio(file_id, audio_path, detail=detail)

            # Track highest epoch for sync state
            if start_time > max_epoch:
                max_epoch = start_time

        except requests.HTTPError as e:
            print(f"    ERROR: {e}", file=sys.stderr)
            errors += 1
        except Exception as e:
            print(f"    ERROR: {e}", file=sys.stderr)
            errors += 1

    # Save sync state
    if max_epoch > last_sync:
        save_sync_state(max_epoch)
        log(f"\nSync state updated to {datetime.fromtimestamp(max_epoch / 1000, tz=timezone.utc).isoformat()}")

    log(f"\nDone: {pulled} pulled, {skipped} skipped, {errors} errors")

    if unresolved_speakers:
        log("\nUnresolved speakers (pin in _db/plaud-speaker-map.json, or assign in Plaud):")
        for spk, n in sorted(unresolved_speakers.items(), key=lambda x: -x[1]):
            log(f"  - {spk}  ({n} recording(s))")

    # Output summary for w-daily integration
    summary = {
        "pulled": pulled,
        "skipped": skipped,
        "errors": errors,
        "unresolved_speakers": unresolved_speakers,
        "files": [
            {
                "filename": f.get("filename", "untitled"),
                "date": datetime.fromtimestamp(f["start_time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                "duration": format_duration(f.get("duration", 0)),
            }
            for f in new_files[:pulled]
        ],
    }
    # Write summary for pipeline consumption
    summary_path = DB_DIR / "plaud-pull-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
