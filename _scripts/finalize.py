#!/usr/bin/env python3
"""finalize.py: deterministic finalizer for the /w-daily v2 pipeline.

v2 inverts the old flow: LLM agents Write complete markdown notes (YAML
frontmatter + body) into _db/staged-notes/<output_filename>. finalize.py is the
deterministic post-pass. It is manifest-driven (_db/manifest.json, written by
classify-inbox.py): it validates each staged note, applies deterministic
body/frontmatter hygiene, moves the note to its interaction/reference home, and
does all bookkeeping (source cleanup, ingest-log, thread-index, open-actions).

Replaces write-notes.py, the envelope-era writer that took agent JSON and did
its own serialization. Here the agent's frontmatter text is authoritative and is
kept verbatim except for a surgical summary patch, so nothing is lost to a
round-trip through a lossy YAML serializer.

Usage:
    python finalize.py --vault PATH [--manifest FILE] [--skips FILE] [--extra-log FILE]
"""

import argparse
import glob as globmod
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

import yaml

from utils import (
    ensure_utf8_stdio,
    atomic_text_write,
    atomic_json_write,
    apply_task_hygiene,
    load_vip_slugs,
    load_vip_tiers,
    normalize_subject,
)


# Required frontmatter per note type (folded from validate-notes.py). The
# validator there stays in place for vault-wide audits; this is the ingestion
# gate. Interaction notes (email/meeting) and reference docs are all finalize
# handles; async falls back to the base interaction set.
REQUIRED_FIELDS = {
    "email": ["date", "type", "interaction-type", "from", "to", "subject",
              "relevance", "summary", "source-file"],
    "meeting": ["date", "type", "interaction-type", "meeting-type", "summary",
                "source-file"],
    "reference": ["date", "type", "source-file"],
}
BASE_REQUIRED = ["date", "type", "interaction-type", "summary", "source-file"]

SUMMARY_MAX = 120

# PII leak (email bodies are sanitized to [EMAIL-xxxx]/[PHONE-xxxx] upstream, so a
# raw address/number in a finalized email body is a leak). From validate-notes.py.
RAW_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
RAW_PHONE_RE = re.compile(r"(?<!\w)\+\d[\d ()\-]{7,}\d")

# Matches the frontmatter fences and captures the YAML body between them.
_FENCE_RE = re.compile(r"^---[ \t]*\r?\n(.*?\r?\n)---[ \t]*(?:\r?\n|$)", re.DOTALL)
# [[Wikilink]] / [[Wikilink|display]] (for summary sanitizing).
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+?)(?:\|[^\[\]]+?)?\]\]")


# ---------- staged-note parsing ----------

def _quote_summary_line(header_text: str):
    """Double-quote the value on a bare (unquoted) summary line, escaping inner
    quotes. Returns the patched header, or None if there is no bare summary line
    to fix. A summary like `summary: All-hands: wrap` breaks YAML because of
    the inner `: `; quoting it is the unambiguous mechanical fix."""
    m = re.search(r"(?m)^summary:[ \t]*(.*)$", header_text)
    if not m:
        return None
    value = m.group(1).strip()
    if not value or value[0] in "\"'":
        return None  # empty or already quoted: nothing to fix
    quoted = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return header_text[:m.start(1)] + quoted + header_text[m.end(1):]


def load_staged(text: str):
    """Split a staged note into (fm dict, header_text, body, warnings).

    header_text is the raw frontmatter block including both fences and the
    trailing newline (header_text + body reconstructs the file exactly). The dict
    is only for validation and hygiene input; the agent's frontmatter text is
    kept as-is so a lossy serializer never touches it. Returns None if there is
    no frontmatter or the YAML still does not parse after the summary-quote retry.
    """
    m = _FENCE_RE.match(text)
    if not m:
        return None
    header_text = text[:m.end()]
    body = text[m.end():]
    warnings = []
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        # Belt-and-suspenders: the common agent slip is a bare-scalar summary
        # containing ": ". Double-quote just that line and retry once.
        patched = _quote_summary_line(header_text)
        if patched is None:
            return None
        m2 = _FENCE_RE.match(patched)
        if not m2:
            return None
        try:
            fm = yaml.safe_load(m2.group(1))
        except yaml.YAMLError:
            return None
        header_text = patched
        warnings.append("auto-quoted summary to recover unparseable frontmatter")
    if not isinstance(fm, dict):
        return None
    # Coerce date/datetime scalars to ISO strings so downstream f-strings and
    # year-slicing see text, not date objects (PyYAML auto-parses `2026-07-03`).
    for key, value in list(fm.items()):
        if hasattr(value, "isoformat"):
            fm[key] = value.isoformat()
    return fm, header_text, body, warnings


# ---------- validation (folded from validate-notes.py) ----------

def validate_note(fm: dict, body: str) -> list:
    """Return a list of hard-failure issues. Empty list means valid.

    Hard failures quarantine the note. Wikilinks in summary are NOT a failure
    (they are auto-stripped later with a warning).
    """
    issues = []
    interaction_type = fm.get("interaction-type", "")
    note_type = fm.get("type", "")

    if interaction_type == "email":
        required = REQUIRED_FIELDS["email"]
    elif interaction_type == "meeting":
        required = REQUIRED_FIELDS["meeting"]
    elif note_type == "reference":
        required = REQUIRED_FIELDS["reference"]
    else:
        required = BASE_REQUIRED

    is_sent_email = interaction_type == "email" and fm.get("direction") == "sent"
    for field in required:
        if is_sent_email and field == "to":
            continue  # sent emails may have an empty recipient set
        val = fm.get(field)
        if field not in fm:
            issues.append(f"missing required field: {field}")
        elif val is None or (isinstance(val, str) and not val.strip()):
            issues.append(f"empty required field: {field}")

    summary = fm.get("summary")
    if isinstance(summary, str) and len(summary.strip()) > SUMMARY_MAX:
        issues.append(f"summary exceeds {SUMMARY_MAX} chars ({len(summary.strip())})")

    # PII tokenization is applied to email bodies only; a raw hit there is a leak.
    if interaction_type == "email":
        if RAW_EMAIL_RE.search(body):
            issues.append("un-tokenized email address in body (PII leak)")
        if RAW_PHONE_RE.search(body):
            issues.append("un-tokenized phone number in body (PII leak)")

    return issues


def target_dir_for(fm: dict, vault: Path):
    """Map a note's type to its home directory. Returns (Path, None) or
    (None, error) when the type is unroutable."""
    note_type = fm.get("type", "")
    if note_type in ("email", "meeting", "async"):
        date = str(fm.get("date", ""))
        year = date[:4]
        if not re.fullmatch(r"\d{4}", year):
            return None, f"cannot derive year from date '{date}'"
        return vault / "05-Interactions" / year, None
    if note_type == "reference":
        return vault / "08-Reference", None
    return None, f"unroutable note type '{note_type}'"


# {date}-{type}-{slug}.md, where date carries its own hyphens and type is a
# meeting subtype. Used to honor an agent's meeting-type correction in the name.
_MEETING_FN_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})-(general|1on1|steerco|sync)-(.+)\.md$")


def corrected_meeting_filename(output_filename: str, fm: dict) -> tuple:
    """Return (final_filename, was_renamed).

    An agent may correct `meeting-type` in a staged note's frontmatter (the
    manifest guessed 1on1 but it is a general all-hands) while keeping the
    assigned filename. When the frontmatter type differs from the filename's type
    segment, rename the destination to match. Non-meeting notes and already-matching
    names pass through unchanged.
    """
    if fm.get("type") != "meeting":
        return output_filename, False
    mt = fm.get("meeting-type")
    if mt not in ("general", "1on1", "steerco", "sync"):
        return output_filename, False
    m = _MEETING_FN_RE.match(output_filename)
    if not m or m.group(2) == mt:
        return output_filename, False
    return f"{m.group(1)}-{mt}-{m.group(3)}.md", True


# ---------- path + unicode helpers ----------

def assert_within_vault(path: Path, vault: Path) -> Path:
    """Resolve `path` and fail closed if it escapes the vault root.

    Target dirs derive from frontmatter (a malformed `date` could push a note
    out of the tree) and source paths come from the manifest. Raises ValueError
    on escape so the per-item handler quarantines/logs instead of writing,
    deleting, or moving outside the vault.
    """
    resolved = path.resolve()
    vault_resolved = vault.resolve()
    if resolved != vault_resolved and vault_resolved not in resolved.parents:
        raise ValueError(f"path escapes vault root: {path}")
    return resolved


def resolve_collision(filepath: Path) -> Path:
    """If filepath exists, append -2, -3, etc. before the extension. Used only
    for _attachments/ destinations (final note paths quarantine on collision)."""
    if not filepath.exists():
        return filepath
    stem, suffix, parent = filepath.stem, filepath.suffix, filepath.parent
    counter = 2
    while True:
        candidate = parent / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def resolve_existing(path: Path):
    """Return the on-disk path for `path`, tolerant of NFC/NFD unicode mismatch.

    A filename with a decomposable character (e.g. đ) can be stored in a
    different normal form than the manifest string, so an exact-path unlink
    silently no-ops and the source survives. Try both normal forms directly
    (correct for the named failure), then a lossy ASCII-prefix glob as a last
    resort. Returns None if nothing matches.
    """
    if path.exists():
        return path
    parent = path.parent
    if not parent.is_dir():
        return None
    for form in ("NFC", "NFD"):
        cand = parent / unicodedata.normalize(form, path.name)
        if cand.exists():
            return cand
    # Last resort: glob first 40 chars with non-ASCII replaced by ? (spec fallback).
    prefix = "".join(globmod.escape(c) if ord(c) < 128 else "?" for c in path.name[:40])
    matches = list(parent.glob(prefix + "*"))
    return matches[0] if len(matches) == 1 else None


# ---------- summary + screenshot body transforms (ported) ----------

def strip_wikilinks(text: str) -> str:
    """Strip [[wikilinks]], keeping the label and converting Hyphen-Names to
    spaces ('Sam-Rivera' -> 'Sam Rivera'). Summaries must be plain text."""
    def _replace(match: re.Match) -> str:
        target = match.group(1).strip()
        parts = target.split("-")
        if len(parts) >= 2 and all(p[:1].isupper() for p in parts if p):
            return " ".join(parts)
        return target
    return _WIKILINK_RE.sub(_replace, text)


def sanitize_summary_in_header(header_text: str, fm: dict) -> tuple:
    """If summary has wikilinks, strip them and patch the summary line in the
    frontmatter text (surgical, so the rest of the header is untouched)."""
    warnings = []
    summary = fm.get("summary")
    if isinstance(summary, str) and "[[" in summary:
        cleaned = strip_wikilinks(summary)
        fm["summary"] = cleaned
        patched, n = re.subn(
            r"(?m)^summary:.*$",
            "summary: " + json.dumps(cleaned, ensure_ascii=False),
            header_text, count=1,
        )
        if n:
            header_text = patched
            warnings.append(f"summary had wikilinks, stripped to plain text: '{cleaned[:60]}'")
        else:
            warnings.append("summary had wikilinks but no 'summary:' line found to patch")
    return header_text, warnings


def _wikilink_slug(val):
    """Extract the slug from a `[[Slug]]` / `[[Slug|display]]` wikilink string."""
    if not isinstance(val, str):
        return None
    m = re.search(r"\[\[([^\[\]|]+)", val)
    return m.group(1).strip() if m else None


def _note_people_slugs(fm: dict) -> set:
    """Collect the people wikilink slugs referenced in a note's frontmatter."""
    slugs = set()
    for field in ("attendees", "to", "cc"):
        for item in fm.get(field) or []:
            s = _wikilink_slug(item)
            if s:
                slugs.add(s)
    for field in ("person", "from"):
        s = _wikilink_slug(fm.get(field))
        if s:
            slugs.add(s)
    return slugs


def _insert_before_closing_fence(header_text: str, block: str) -> str:
    """Insert `block` (its own lines) just before the closing `---` fence."""
    m = re.search(r"\r?\n---[ \t]*\r?\n?$", header_text)
    if not m:
        return header_text
    return header_text[:m.start()] + "\n" + block.rstrip("\n") + header_text[m.start():]


def _append_tag_items(header_text: str, tags: list) -> str:
    """Append `- <tag>` items under an existing block-style `tags:` key."""
    m = re.search(r"(?m)^tags:[ \t]*$", header_text)
    if not m:
        return header_text  # inline/absent tags: skip rather than risk corruption
    block = "".join(f"\n  - {t}" for t in tags)
    return header_text[:m.end()] + block + header_text[m.end():]


def stamp_vip(header_text: str, fm: dict, vip_tiers: dict):
    """Stamp vip-involved/tags onto a meeting/email note whose participants carry
    a VIP tier in the registry but which lacks vip-involved (attendees did not
    resolve upstream, so the manifest could not pre-compute it). Returns
    (header_text, stamped_tiers). Notes that already carry vip-involved are left
    untouched: the manifest pre-computation wins.
    """
    if fm.get("vip-involved") or fm.get("type") not in ("meeting", "email"):
        return header_text, None
    found = sorted({vip_tiers[s] for s in _note_people_slugs(fm) if s in vip_tiers})
    if not found:
        return header_text, None
    inject = ["vip-involved:"] + [f"  - {t}" for t in found]
    if fm.get("tags"):
        # A tags: key already exists; append vip tags to it rather than emit a
        # duplicate tags: key (which would make the YAML ambiguous).
        header_text = _insert_before_closing_fence(header_text, "\n".join(inject))
        header_text = _append_tag_items(header_text, [f"vip/{t}" for t in found])
    else:
        inject += ["tags:"] + [f"  - vip/{t}" for t in found]
        header_text = _insert_before_closing_fence(header_text, "\n".join(inject))
    fm["vip-involved"] = found
    return header_text, found


def rewrite_screenshot_wikilinks(body: str, basenames: list, stem: str) -> str:
    """Rewrite `![[basename]]` to `![[screenshots/<stem>/basename]]` so Obsidian
    resolves the link after the PNGs move out of 00-Inbox/_screenshots/."""
    if not body or not basenames or not stem:
        return body
    for basename in basenames:
        if not basename:
            continue
        pattern = re.compile(r"!\[\[" + re.escape(basename) + r"(?:\|[^\[\]]+)?\]\]")
        body = pattern.sub(f"![[screenshots/{stem}/{basename}]]", body)
    return body


def rewrite_self_wikilinks(body: str, old_stem: str, new_stem: str) -> str:
    """Repoint a note's self-references after its destination filename changed.

    Agents are told to emit `[source:: [[<own filename>]]]` using the filename the
    manifest assigned, and (for meetings) to correct `meeting-type` while KEEPING
    that filename so the pipeline can rename. `corrected_meeting_filename` then
    renames the file, and `resolve_collision` may add a `-N` suffix on top, but the
    body still cites the pre-rename stem: a link to a note that no longer exists,
    or (worse, when the old stem is a real note) a silent misattribution that
    resolves to the wrong note and so never shows up as a broken link.

    Matches the wikilink target, not the `[source::` field, so aliased forms and
    any other field citing the stem are repointed too. Alias text is preserved.
    """
    if not body or not old_stem or old_stem == new_stem:
        return body
    pattern = re.compile(r"\[\[" + re.escape(old_stem) + r"(\|[^\[\]]*)?\]\]")
    return pattern.sub(lambda m: f"[[{new_stem}{m.group(1) or ''}]]", body)


def apply_task_hygiene_to_body(body: str, fm: dict, vip_slugs: set) -> str:
    """Run apply_task_hygiene (VIP-aware, size-based; stamps [created::]) over
    each body line."""
    return "\n".join(
        apply_task_hygiene(line, fm, vip_slugs=vip_slugs)
        for line in body.split("\n")
    )


# ---------- ingest-log (ported) ----------

def load_ingest_log(vault: Path) -> list:
    log_path = vault / "_db" / "ingest-log.json"
    if log_path.exists():
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def dedup_log_entry(log: list, entry: dict) -> bool:
    """Return True if `entry` should be skipped. A prior 'created' whose
    output-file still exists wins (skip). A prior 'skipped-*' is replaced when the
    new entry is a 'created' (upgrade). Ghost 'created' entries (output missing)
    let re-processing through."""
    source = entry.get("source-file", "")
    for existing in log:
        if existing.get("source-file") == source:
            if existing.get("action") == "created":
                output = existing.get("output-file", "")
                if output and os.path.exists(output):
                    return True
                return False  # ghost entry, allow re-processing
            if str(existing.get("action", "")).startswith("skipped"):
                if entry.get("action") == "created":
                    log.remove(existing)
                    return False
                return True
    return False


def update_thread_index(vault: Path, email_notes: list) -> None:
    """Append written email notes to _db/thread-index.json, deduped by path in
    each bucket (folds update-thread-index.py). email_notes: [(rel_path, fm)]."""
    if not email_notes:
        return
    index_path = vault / "_db" / "thread-index.json"
    index = {}
    if index_path.exists():
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                index = json.load(f)
        except Exception:
            index = {}
    by_conv = index.get("by_conversation_id", {})
    by_subj = index.get("by_normalized_subject", {})

    for rel, fm in email_notes:
        conv = fm.get("conversation-id")
        subject = fm.get("subject")
        if not conv and not subject:
            continue
        entry = {
            "path": rel.replace("\\", "/"),
            "date": str(fm.get("date", "")),
            "subject": subject or "",
            "relevance": fm.get("relevance", ""),
        }
        if conv:
            lst = by_conv.setdefault(str(conv), [])
            if entry["path"] not in {e["path"] for e in lst}:
                lst.append(entry)
        if subject:
            norm = normalize_subject(subject)
            if norm:
                lst = by_subj.setdefault(norm, [])
                if entry["path"] not in {e["path"] for e in lst}:
                    lst.append(entry)

    atomic_json_write(index_path, {"by_conversation_id": by_conv,
                                   "by_normalized_subject": by_subj})


# ---------- source handling ----------

def move_to_attachments(src_path: Path, vault: Path, result: dict) -> None:
    """Move a source (and, for a transcript .txt, its same-stem .json/.md
    companions) into _attachments/. Companions carry the canonical metadata and
    preview the note was built from, worth keeping with the original."""
    attachments = vault / "_attachments"
    attachments.mkdir(parents=True, exist_ok=True)
    real = resolve_existing(src_path)
    if not real:
        return
    dest = resolve_collision(attachments / real.name)
    shutil.move(str(real), str(dest))
    result["moved_to_attachments"].append(str(dest.relative_to(vault)))
    suffix = real.suffix.lower()
    if suffix in (".txt", ".md"):
        companion_exts = (".json", ".md") if suffix == ".txt" else (".json",)
        for comp_ext in companion_exts:
            comp = resolve_existing(src_path.with_suffix(comp_ext))
            if comp and comp.exists():
                comp_dest = resolve_collision(attachments / comp.name)
                shutil.move(str(comp), str(comp_dest))
                result["moved_to_attachments"].append(str(comp_dest.relative_to(vault)))


def move_screenshots(screenshots: list, stem: str, vault: Path, result: dict) -> None:
    """Move screenshots into _attachments/screenshots/<stem>/ alongside the
    transcript; body wikilinks were already rewritten to this path."""
    if not screenshots or not stem:
        return
    target = vault / "_attachments" / "screenshots" / stem
    for shot in screenshots:
        raw = shot.get("path")
        if not raw:
            continue
        src = Path(raw) if os.path.isabs(raw) else vault / raw
        real = resolve_existing(src)
        if not real:
            result["warnings"].append(f"screenshot source missing: {shot.get('basename', raw)}")
            continue
        target.mkdir(parents=True, exist_ok=True)
        dest = resolve_collision(target / real.name)
        shutil.move(str(real), str(dest))
        result["moved_to_attachments"].append(str(dest.relative_to(vault)))


def move_email_attachments(names: list, stamp: str, vault: Path, result: dict) -> None:
    """Move staged email attachments into _attachments/email/<stamp>/, matching
    the wikilinks classify-inbox already wrote into the note's frontmatter.

    Keyed on the receive-second stamp rather than the email's .txt stem: the stem
    is a subject slug that can run ~90 chars and carry emoji, while the stamp is
    short, unique per message, and already the correlation key.
    """
    if not names or not stamp:
        return
    staging = vault / "00-Inbox" / "_email-attachments"
    target = vault / "_attachments" / "email" / stamp
    for name in names:
        real = resolve_existing(staging / name)
        if not real:
            result["warnings"].append(f"email attachment source missing: {name}")
            continue
        target.mkdir(parents=True, exist_ok=True)
        dest = target / real.name
        if dest.exists():
            # Same stamp + same name means the same file: a re-run, not a clash.
            # Renaming would strand the frontmatter wikilink that points here.
            result["warnings"].append(f"email attachment already present: {stamp}/{name}")
            continue
        shutil.move(str(real), str(dest))
        result["moved_to_attachments"].append(str(dest.relative_to(vault)))


def delete_source(src_path: Path, vault: Path, result: dict) -> None:
    """Unicode-robust delete of an in-vault source; records the relative path."""
    real = resolve_existing(src_path)
    if real and real.exists():
        real.unlink()
        result["deleted_sources"].append(str(real.relative_to(vault)))


# ---------- main ----------

def main():
    parser = argparse.ArgumentParser(description="Finalize v2 staged notes into the vault")
    parser.add_argument("--vault", default=".", help="Vault root directory")
    parser.add_argument("--manifest", default=None,
                        help="Manifest path (default: <vault>/_db/manifest.json)")
    parser.add_argument("--skips", default=None,
                        help="JSON list of {source_file, reason, move_to_attachments} from agent SKIP returns")
    parser.add_argument("--extra-log", default=None,
                        help="JSON list of ready-made ingest-log entries (e.g. manual-note merges)")
    args = parser.parse_args()

    ensure_utf8_stdio()
    vault = Path(args.vault)
    manifest_path = Path(args.manifest) if args.manifest else vault / "_db" / "manifest.json"

    result = {
        "written": [], "renamed": [], "vip_stamped": [], "quarantined": [],
        "skipped": [], "deleted_sources": [], "moved_to_attachments": [],
        "logged": 0, "touched_dates": [], "warnings": [], "errors": [],
    }

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # output_filename -> {source, ctype, screenshots}. Each email/transcript/doc
    # is expected to produce its own staged note; thread-folded emails come
    # through --skips, not here.
    expected = {}
    for e in manifest.get("email_manifest", []):
        ofn = e.get("output_filename")
        if ofn:
            expected[ofn] = {"source": e.get("file"), "ctype": "email",
                             "attachments": e.get("attachments") or [],
                             "attachment_stamp": e.get("attachment_stamp") or ""}
    for t in manifest.get("transcripts", []):
        ofn = t.get("output_filename")
        if ofn:
            expected[ofn] = {"source": t.get("file"), "ctype": "transcript",
                             "screenshots": t.get("screenshots") or []}
    for d in manifest.get("docs", []):
        ofn = d.get("output_filename")
        if ofn:
            expected[ofn] = {"source": d.get("file"), "ctype": "doc",
                             "is_email_attachment": d.get("is_email_attachment", False),
                             "source_email": d.get("source_email")}

    ingest_log = load_ingest_log(vault)
    vip_slugs = load_vip_slugs(vault)
    vip_tiers = load_vip_tiers(vault)
    log_entries = []          # created + failed, deduped/appended at the end
    written_email_notes = []  # (rel_path, fm) for the thread index
    written_dates = set()     # date: of every written note, feeds the briefing builder

    staged_dir = vault / "_db" / "staged-notes"
    quarantine_dir = staged_dir / "_quarantine"
    staged_files = sorted(staged_dir.glob("*.md")) if staged_dir.is_dir() else []

    def quarantine(staged: Path, reason: str) -> str:
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        dest = resolve_collision(quarantine_dir / staged.name)
        shutil.move(str(staged), str(dest))
        rel = str(dest.relative_to(vault))
        result["quarantined"].append(rel)
        result["warnings"].append(f"quarantined {staged.name}: {reason}")
        return rel

    for staged in staged_files:
        ofn = staged.name
        exp = expected.get(ofn)
        if exp is None:
            quarantine(staged, "not in manifest (unknown staged file)")
            continue

        try:
            raw = staged.read_text(encoding="utf-8")
        except Exception as ex:
            quarantine(staged, f"unreadable: {ex}")
            continue

        parsed = load_staged(raw)
        source_name = Path(exp["source"]).name if exp.get("source") else ofn
        if parsed is None:
            quarantine(staged, "no parseable YAML frontmatter")
            log_entries.append({
                "source-file": source_name, "action": "failed", "output-file": None,
                "type": exp["ctype"], "summary": "invalid frontmatter",
            })
            continue

        fm, header_text, body, load_warnings = parsed
        for w in load_warnings:
            result["warnings"].append(f"{ofn}: {w}")
        issues = validate_note(fm, body)
        if issues:
            reason = "; ".join(issues)
            quarantine(staged, reason)
            log_entries.append({
                "source-file": source_name, "action": "failed", "output-file": None,
                "type": exp["ctype"], "date": str(fm.get("date", "")),
                "summary": f"validation failed: {reason}",
            })
            continue

        target_dir, err = target_dir_for(fm, vault)
        if err:
            quarantine(staged, err)
            result["errors"].append(f"{ofn}: {err}")
            continue
        # Honor an agent's meeting-type correction in the destination name. The
        # corrected name skipped classify's collision pre-resolution, so it gets
        # -2 handling; the pre-checked original name keeps quarantine-on-collision.
        final_name, was_renamed = corrected_meeting_filename(ofn, fm)
        final_path = target_dir / final_name
        try:
            assert_within_vault(final_path, vault)
        except ValueError as ex:
            quarantine(staged, str(ex))
            result["errors"].append(f"{ofn}: {ex}")
            continue
        if was_renamed:
            final_path = resolve_collision(final_path)
        elif final_path.exists():
            quarantine(staged, f"target already exists: {final_path.relative_to(vault)}")
            result["errors"].append(f"{ofn}: target exists, not overwriting")
            continue

        # Deterministic body/frontmatter hygiene before the move.
        header_text, warns = sanitize_summary_in_header(header_text, fm)
        for w in warns:
            result["warnings"].append(f"{ofn}: {w}")
        # Stamp VIP frontmatter the manifest could not pre-compute (attendees that
        # only resolve at finalize time, e.g. a placeholder recorder-import transcript).
        header_text, vip_found = stamp_vip(header_text, fm, vip_tiers)
        if exp["ctype"] == "transcript" and exp.get("screenshots"):
            stem = Path(exp["source"]).stem
            basenames = [s.get("basename") for s in exp["screenshots"] if s.get("basename")]
            body = rewrite_screenshot_wikilinks(body, basenames, stem)
        # Anchored to final_path, not final_name: resolve_collision may have added a
        # -N suffix after the rename, and citing final_name would desync again.
        body = rewrite_self_wikilinks(body, Path(ofn).stem, final_path.stem)
        body = apply_task_hygiene_to_body(body, fm, vip_slugs)

        try:
            atomic_text_write(final_path, header_text + body)  # mkdirs parent
            staged.unlink()
        except Exception as ex:
            result["errors"].append(f"failed to write {final_path.relative_to(vault)}: {ex}")
            continue

        rel = str(final_path.relative_to(vault))
        result["written"].append(rel)
        written_dates.add(str(fm.get("date", ""))[:10])
        if was_renamed:
            result["renamed"].append({"from": ofn, "to": final_path.name})
        if vip_found:
            result["vip_stamped"].append(rel)

        # Source cleanup per content type.
        source = exp.get("source")
        if source:
            src_path = Path(source) if os.path.isabs(source) else vault / source
            try:
                assert_within_vault(src_path, vault)
                if exp["ctype"] == "transcript":
                    stem = src_path.stem
                    move_to_attachments(src_path, vault, result)
                    move_screenshots(exp.get("screenshots") or [], stem, vault, result)
                elif exp["ctype"] == "email":
                    move_email_attachments(exp.get("attachments") or [],
                                           exp.get("attachment_stamp") or "",
                                           vault, result)
                    delete_source(src_path, vault, result)
                elif exp["ctype"] == "doc" and exp.get("is_email_attachment"):
                    # A promoted email attachment: its raw file is moved to
                    # _attachments/email/<stamp>/ by the parent email's
                    # move_email_attachments and stays linked from the email.
                    # Don't delete it here (that would strand that wikilink).
                    pass
                else:
                    delete_source(src_path, vault, result)
            except ValueError as ex:
                result["errors"].append(f"source escapes vault, left in place: {ex}")
            except Exception as ex:
                result["errors"].append(f"failed to clean source {source}: {ex}")

        # Ingest-log + thread-index feed from the note's own (authoritative) fm.
        log_entries.append({
            "source-file": source_name, "action": "created", "output-file": rel,
            "type": "meeting" if exp["ctype"] == "transcript" else exp["ctype"],
            "date": str(fm.get("date", "")),
            "subject": fm.get("subject", ""),
            "summary": fm.get("summary", ""),
        })
        if exp["ctype"] == "email":
            written_email_notes.append((rel, fm))

    # definitive_lows: log-only, then delete the source.
    for low in manifest.get("definitive_lows", []):
        source = low.get("file")
        if not source:
            continue
        src_path = Path(source) if os.path.isabs(source) else vault / source
        try:
            assert_within_vault(src_path, vault)
            delete_source(src_path, vault, result)
        except Exception as ex:
            result["errors"].append(f"failed to delete low source {source}: {ex}")
        log_entries.append({
            "source-file": Path(source).name, "action": "skipped-low-relevance",
            "output-file": None, "type": "email", "subject": low.get("subject", ""),
            "date": low.get("date", ""), "summary": low.get("low_reason", ""),
        })

    # pre_skipped: emails classify matched against the ingest-log as already
    # processed. Log the duplicate and delete the redundant source.
    for ps in manifest.get("pre_skipped", []):
        source = ps.get("file")
        name = ps.get("filename") or (Path(source).name if source else "")
        if source:
            src_path = Path(source) if os.path.isabs(source) else vault / source
            try:
                assert_within_vault(src_path, vault)
                delete_source(src_path, vault, result)
            except Exception as ex:
                result["errors"].append(f"failed to delete pre-skipped source {source}: {ex}")
        result["skipped"].append(name)
        log_entries.append({
            "source-file": name, "action": "skipped-duplicate", "output-file": None,
            "type": "email", "subject": ps.get("subject", ""),
            "date": ps.get("date", ""), "summary": ps.get("reason", ""),
        })

    # skipped_transcripts: recovered/zero-duration duplicate recordings. Keep the
    # verbatim recording (move to _attachments/ with companions), just log the skip.
    for st in manifest.get("skipped_transcripts", []):
        source = st.get("file")
        name = Path(source).name if source else ""
        if source:
            src_path = Path(source) if os.path.isabs(source) else vault / source
            try:
                assert_within_vault(src_path, vault)
                move_to_attachments(src_path, vault, result)
            except Exception as ex:
                result["errors"].append(f"failed to move skipped transcript {source}: {ex}")
        result["skipped"].append(name)
        log_entries.append({
            "source-file": name, "action": "skipped-duplicate", "output-file": None,
            "type": "meeting", "subject": st.get("subject", ""),
            "summary": st.get("reason", ""),
        })

    # --skips: agent SKIP returns (duplicates, thread-folded emails, etc).
    if args.skips:
        with open(args.skips, "r", encoding="utf-8") as f:
            skips = json.load(f)
        for skip in skips:
            sf = skip.get("source_file")
            if not sf:
                continue
            p = Path(sf)
            if p.is_absolute():
                src_path = p
            elif "/" in sf or os.sep in sf:
                src_path = vault / sf
            else:
                src_path = vault / "00-Inbox" / "_processing" / sf
            try:
                assert_within_vault(src_path, vault)
                if skip.get("move_to_attachments"):
                    move_to_attachments(src_path, vault, result)
                else:
                    delete_source(src_path, vault, result)
            except Exception as ex:
                result["errors"].append(f"failed to handle skip source {sf}: {ex}")
            result["skipped"].append(Path(sf).name)
            log_entries.append({
                "source-file": Path(sf).name,
                "action": skip.get("action", "skipped-duplicate"),
                "output-file": None, "type": skip.get("type", "email"),
                "date": skip.get("date", ""), "summary": skip.get("reason", ""),
            })

    # --extra-log: ready-made entries from main-context work (e.g. manual-note merges).
    if args.extra_log:
        with open(args.extra_log, "r", encoding="utf-8") as f:
            log_entries.extend(json.load(f))

    # Append all log entries through the dedup guard, stamping timestamps.
    for entry in log_entries:
        if not entry.get("timestamp"):
            entry["timestamp"] = datetime.now().isoformat()
        if not dedup_log_entry(ingest_log, entry):
            ingest_log.append(entry)
            result["logged"] += 1
    if result["logged"] > 0:
        atomic_json_write(vault / "_db" / "ingest-log.json", ingest_log)

    update_thread_index(vault, written_email_notes)

    result["touched_dates"] = sorted(written_dates - {""})

    if result["written"]:
        try:
            subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parent / "build-open-actions.py"),
                 "--vault", str(vault)],
                check=False, capture_output=True, text=True,
            )
        except Exception as ex:
            result["warnings"].append(f"build-open-actions failed: {ex}")

    # Purge the sanitized transcript working copies classify staged for the
    # agents. By now their notes are finalized or quarantined, so the copies are
    # stale; the next run regenerates them fresh from the sources.
    agent_inputs = vault / "_db" / "agent-inputs"
    if agent_inputs.is_dir():
        for f in agent_inputs.glob("*"):
            try:
                if f.is_file():
                    f.unlink()
            except Exception as ex:
                result["warnings"].append(f"could not remove agent-input {f.name}: {ex}")

    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
