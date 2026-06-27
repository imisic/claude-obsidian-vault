#!/usr/bin/env python3
"""
write-notes.py: Deterministic note writer for Vault pipeline.

Takes structured JSON from email-processor or transcript-processor agents,
writes notes to disk, deletes source files, and updates ingest-log.

Usage:
    python3 write-notes.py --vault PATH [--input FILE | stdin]

Input JSON format:
{
  "notes": [
    {
      "output_path": "05-Interactions/2026/note.md",
      "frontmatter": { "date": "...", "type": "email", ... },
      "body_text": "# Subject\n\nBody with [[wikilinks]]...",   # `body` accepted as alias
      "source_files": ["00-Inbox/_processing/original.txt"],
      "briefing_data": { "date": "...", "subject": "...", "summary": "..." }
    }
  ],
  "log_entries": [
    { "source-file": "...", "action": "created", "output-file": "...", ... }
  ],
  "skipped_log_entries": [
    { "source-file": "...", "action": "skipped-low-relevance", ... }
  ]
}

Output JSON (stdout):
{
  "written": ["05-Interactions/2026/note.md", ...],
  "deleted": ["00-Inbox/_processing/file.txt", ...],
  "logged": 5,
  "errors": ["error message", ...]
}
"""

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from utils import ensure_utf8_stdio, atomic_text_write, apply_task_hygiene, load_vip_slugs


# Matches [[Wikilink]] and [[Wikilink|display]]
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+?)(?:\|[^\[\]]+?)?\]\]")


def apply_task_hygiene_to_body(body: str, frontmatter: dict, vip_slugs: set) -> str:
    """Run apply_task_hygiene over each line of body text."""
    return "\n".join(
        apply_task_hygiene(line, frontmatter, vip_slugs=vip_slugs)
        for line in body.split("\n")
    )


def _split_content(content: str) -> tuple[dict, str] | None:
    """Split an agent-emitted `content` string (full markdown doc) into
    a frontmatter dict and body_text string. Returns None if the content
    has no `---` fences.

    Defensive parser: agents sometimes invent a `content` field combining
    frontmatter and body. We accept it and convert here so a schema slip
    doesn't force a SendMessage round-trip (cost: ~5 min per occurrence).
    """
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", content, re.DOTALL)
    if not m:
        return None
    fm_yaml, body = m.group(1), m.group(2)
    # Parse YAML with a minimal scalar-only loader to avoid pulling PyYAML
    # if it's not installed; fall back to PyYAML if scalars aren't enough.
    try:
        import yaml
        fm = yaml.safe_load(fm_yaml) or {}
    except ImportError:
        fm = _parse_simple_yaml(fm_yaml)
    # Coerce date/datetime values to ISO strings, JSON-serialization safety.
    for key, value in list(fm.items()):
        if isinstance(value, datetime):
            fm[key] = value.isoformat()
        elif hasattr(value, "isoformat"):  # datetime.date
            fm[key] = value.isoformat()
    return fm, body.lstrip("\n")


def _parse_simple_yaml(text: str) -> dict:
    """Minimal YAML scalar/list parser for when PyYAML isn't available.
    Handles `key: value` and `key:\n  - item` only, enough for our frontmatter.
    """
    result = {}
    current_key = None
    for raw in text.split("\n"):
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - "):
            if current_key is not None:
                result.setdefault(current_key, [])
                if isinstance(result[current_key], list):
                    item = line[4:].strip().strip('"').strip("'")
                    result[current_key].append(item)
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if value == "":
                result[key] = []  # might be a list-typed key
                current_key = key
            else:
                result[key] = value
                current_key = None
    # Collapse empty-list keys back to None when we never found a list item
    for k, v in list(result.items()):
        if v == []:
            result[k] = None
    return result


def strip_wikilinks(text: str) -> str:
    """Strip [[wikilinks]] from text, keeping the displayed label but converting
    Hyphen-Separated-Names back to spaces ("Sam-Rivera" → "Sam Rivera").
    Used to sanitize summary frontmatter fields, which the ingestion rules say
    must be plain text.
    """
    if not text:
        return text

    def _replace(match: re.Match) -> str:
        target = match.group(1).strip()
        # If name looks like a person slug (two+ Capitalized parts joined by -),
        # convert to spaces. Else leave the target as-is.
        parts = target.split("-")
        if len(parts) >= 2 and all(p[:1].isupper() for p in parts if p):
            return " ".join(parts)
        return target

    return _WIKILINK_RE.sub(_replace, text)


def sanitize_summary(fm: dict) -> list[str]:
    """Strip wikilinks from the summary field if present. Returns list of warnings."""
    warnings = []
    summary = fm.get("summary")
    if isinstance(summary, str) and "[[" in summary:
        original = summary
        cleaned = strip_wikilinks(summary)
        fm["summary"] = cleaned
        warnings.append(f"Summary had wikilinks, auto-stripped: '{original[:60]}...' → '{cleaned[:60]}...'")
    return warnings


def frontmatter_to_yaml(fm: dict) -> str:
    """Serialize frontmatter dict to YAML string (simple, no library needed)."""
    lines = []
    for key, value in fm.items():
        if value is None:
            continue
        if isinstance(value, list):
            if not value:
                continue
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_yaml_scalar(item)}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {str(value).lower()}")
        elif isinstance(value, int):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {_yaml_scalar(str(value))}")
    return "\n".join(lines)


def _yaml_scalar(val: str) -> str:
    """Quote a YAML scalar if needed."""
    val = str(val)
    # Quote if it contains special chars, starts with special, or is empty
    needs_quote = (
        not val
        or val.startswith(("{", "[", "*", "&", "!", "%", "@", "`"))
        or ":" in val
        or "#" in val
        or val.startswith("- ")
        or val.startswith("? ")
        or val in ("true", "false", "null", "yes", "no", "on", "off")
        or val != val.strip()
        or "\n" in val
    )
    if needs_quote:
        # Use double quotes, escape internal double quotes
        escaped = val.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return val


def assert_within_vault(path: Path, vault: Path) -> Path:
    """Resolve `path` and fail closed if it escapes the vault root.

    Output and source paths come from agent JSON, which is trusted today. A
    malformed `../` or absolute path would otherwise let write-notes write,
    delete, or move files outside the vault (audit finding #3). Raises
    ValueError on escape so the per-note / per-source handlers log it and skip.
    """
    resolved = path.resolve()
    vault_resolved = vault.resolve()
    if resolved != vault_resolved and vault_resolved not in resolved.parents:
        raise ValueError(f"path escapes vault root: {path}")
    return resolved


def resolve_collision(filepath: Path) -> Path:
    """If filepath exists, append -2, -3, etc. before .md extension."""
    if not filepath.exists():
        return filepath
    stem = filepath.stem
    suffix = filepath.suffix
    parent = filepath.parent
    counter = 2
    while True:
        candidate = parent / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def derive_transcript_stem(source_files: list) -> str | None:
    """Return the stem of the .txt transcript in source_files, used to name
    the _attachments/screenshots/<stem>/ subdirectory. Falls back to the
    first source's stem if no .txt is found.
    """
    if not source_files:
        return None
    for sf in source_files:
        p = Path(sf)
        if p.suffix.lower() == ".txt":
            return p.stem
    return Path(source_files[0]).stem


def rewrite_screenshot_wikilinks(body: str, screenshot_files: list, stem: str) -> str:
    """Rewrite basename-only screenshot wikilinks to their final attachments path.

    The transcript-processor agent embeds screenshots as `![[basename.png]]` (basename
    only). This function rewrites each occurrence to `![[screenshots/<stem>/<basename>]]`
    so Obsidian resolves the link after the PNGs are moved out of 00-Inbox/_screenshots/.

    Only rewrites basenames that appear in `screenshot_files`, unrelated `![[...]]`
    links are left untouched.
    """
    if not body or not screenshot_files or not stem:
        return body
    for path_str in screenshot_files:
        basename = Path(path_str).name
        if not basename:
            continue
        # Match ![[basename]] or ![[basename|alias]]
        pattern = re.compile(r"!\[\[" + re.escape(basename) + r"(?:\|[^\[\]]+)?\]\]")
        replacement = f"![[screenshots/{stem}/{basename}]]"
        body = pattern.sub(replacement, body)
    return body


def load_ingest_log(vault: Path) -> list:
    """Load ingest-log.json, return list."""
    log_path = vault / "_db" / "ingest-log.json"
    if log_path.exists():
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            return []
    return []


def save_ingest_log(vault: Path, log: list) -> None:
    """Write ingest-log.json atomically."""
    log_path = vault / "_db" / "ingest-log.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = log_path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp_path), str(log_path))


def dedup_log_entry(log: list, entry: dict) -> bool:
    """Check if entry should be skipped (dedup guard).

    Returns True if entry should be SKIPPED (already exists and valid).
    """
    source = entry.get("source-file", "")
    for existing in log:
        if existing.get("source-file") == source:
            if existing.get("action") == "created":
                # Check if output file actually exists
                output = existing.get("output-file", "")
                if output and os.path.exists(output):
                    return True  # Already processed, skip
                # Ghost entry, allow re-processing
                return False
            if existing.get("action", "").startswith("skipped"):
                if entry.get("action") == "created":
                    # Upgrade: replace skip with created
                    log.remove(existing)
                    return False
                return True  # Already skipped
    return False


def main():
    parser = argparse.ArgumentParser(description="Write notes from structured agent output")
    parser.add_argument("--vault", default=str(Path(__file__).resolve().parent.parent),
                        help="Vault root directory")
    parser.add_argument("--input", default=None,
                        help="Input JSON file (default: stdin)")
    parser.add_argument("--inputs", nargs="+", default=None,
                        help="Multiple input JSON files, processed in one pass "
                             "(avoids per-file interpreter startup). Takes precedence over --input.")
    args = parser.parse_args()

    ensure_utf8_stdio()
    vault = Path(args.vault)

    # Read input: one or many files, or stdin. Multiple files are concatenated
    # into a single batch so collision handling and ingest-log dedup run once
    # over the whole set (and we pay one interpreter startup, not N).
    input_files = args.inputs if args.inputs else ([args.input] if args.input else None)
    if input_files:
        notes, log_entries, skipped_log_entries = [], [], []
        for path in input_files:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            notes.extend(d.get("notes", []))
            log_entries.extend(d.get("log_entries", []))
            skipped_log_entries.extend(d.get("skipped_log_entries", []))
    else:
        data = json.load(sys.stdin)
        notes = data.get("notes", [])
        log_entries = data.get("log_entries", [])
        skipped_log_entries = data.get("skipped_log_entries", [])

    # Normalize log-entry key names: agents inconsistently emit hyphen vs
    # underscore (e.g. `source_file` vs `source-file`). Both refer to the same
    # field; downstream code reads the canonical hyphen form. Without this,
    # a typo silently skips file moves and bloats the ingest-log with malformed
    # entries (cost a real morning run ~3 min to diagnose).
    for entry in log_entries + skipped_log_entries:
        for hyphen_key, underscore_key in (("source-file", "source_file"),
                                            ("output-file", "output_file")):
            if hyphen_key not in entry and underscore_key in entry:
                entry[hyphen_key] = entry.pop(underscore_key)

    result = {
        "written": [],
        "deleted": [],
        "moved_to_attachments": [],
        "skipped_deleted": [],
        "logged": 0,
        "errors": [],
        "warnings": [],
    }

    # Validate note structure before processing.
    # `body_text` is the canonical field, but several aliases are accepted,
    # agents under-spec the contract too often, and losing a whole batch to a
    # field-name typo is the worst-case time cost. Order of precedence:
    #   1. `body_text` (canonical)
    #   2. `body` (legacy alias, triggers warning)
    #   3. `content` (full doc with frontmatter, auto-split, triggers warning)
    REQUIRED_NOTE_KEYS = {"output_path", "frontmatter", "source_files"}
    valid_notes = []
    for i, note in enumerate(notes):
        # Alias resolution: body → body_text
        if "body_text" not in note and "body" in note:
            note["body_text"] = note["body"]
            result["warnings"].append(
                f"Note {i} used 'body' field; aliased to 'body_text' "
                f"(canonical name): {note.get('output_path', '<no path>')}"
            )
        # Alias resolution: content (full doc) → frontmatter + body_text
        # If the agent inlined the whole doc (---\nYAML\n---\n<body>), split it.
        if "content" in note and ("body_text" not in note or "frontmatter" not in note):
            content = note["content"]
            split = _split_content(content)
            if split is not None:
                fm_from_content, body_from_content = split
                if "frontmatter" not in note:
                    note["frontmatter"] = fm_from_content
                if "body_text" not in note:
                    note["body_text"] = body_from_content
                result["warnings"].append(
                    f"Note {i} used 'content' field; auto-split into "
                    f"frontmatter+body_text: {note.get('output_path', '<no path>')}"
                )
            else:
                result["warnings"].append(
                    f"Note {i} 'content' field could not be split "
                    f"(no `---` fences); ignoring: {note.get('output_path', '<no path>')}"
                )
        missing = REQUIRED_NOTE_KEYS - set(note.keys())
        if "body_text" not in note:
            missing = missing | {"body_text"}
        if missing:
            result["errors"].append(
                f"Note {i} missing required keys {missing}: "
                f"{note.get('output_path', '<no path>')}"
            )
            continue
        if not isinstance(note["source_files"], list):
            result["errors"].append(
                f"Note {i} source_files must be a list, got {type(note['source_files']).__name__}: "
                f"{note['output_path']}"
            )
            continue
        # briefing_data is required for the daily-note builder. Warn (not
        # fail): write-notes.py's job is to persist the note. The master
        # skill enforces the briefing_data contract before invoking us.
        bd = note.get("briefing_data")
        if not isinstance(bd, dict):
            result["warnings"].append(
                f"Note {i} missing briefing_data dict, daily briefing will "
                f"skip this note: {note['output_path']}"
            )
        else:
            missing_bd = {"date", "subject", "summary"} - {k for k, v in bd.items() if v}
            if missing_bd:
                result["warnings"].append(
                    f"Note {i} briefing_data missing required fields "
                    f"{missing_bd}: {note['output_path']}"
                )
        valid_notes.append(note)
    notes = valid_notes

    # Load ingest log + VIP slugs once
    ingest_log = load_ingest_log(vault)
    vip_slugs = load_vip_slugs(vault)

    # Write notes
    for note in notes:
        try:
            output_rel = note["output_path"]
            output_path = vault / output_rel

            # Reject paths that escape the vault before touching the filesystem
            assert_within_vault(output_path, vault)

            # Ensure parent directory exists
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Collision handling
            output_path = resolve_collision(output_path)
            actual_rel = str(output_path.relative_to(vault))

            # Build file content
            fm = note.get("frontmatter", {})
            body = note.get("body_text", "")

            # Rewrite screenshot wikilinks from basename-only to final attachments path
            screenshot_files = note.get("screenshot_files") or []
            if screenshot_files:
                stem = derive_transcript_stem(note.get("source_files", []))
                if stem:
                    body = rewrite_screenshot_wikilinks(body, screenshot_files, stem)
                else:
                    result["warnings"].append(
                        f"{note.get('output_path', '?')}: cannot derive stem for screenshot rewrite (no source files)"
                    )

            # Enforce spec: summary must be plain text, no wikilinks
            warnings = sanitize_summary(fm)
            if warnings:
                for w in warnings:
                    result["warnings"].append(f"{note.get('output_path', '?')}: {w}")

            # Apply task hygiene rules per line (deterministic safety net against
            # agent over-extraction)
            body = apply_task_hygiene_to_body(body, fm, vip_slugs)

            content = f"---\n{frontmatter_to_yaml(fm)}\n---\n\n{body}\n"

            # Write atomically with validation
            atomic_text_write(output_path, content)

            result["written"].append(actual_rel)

            # Update output-file in corresponding log entry
            for entry in log_entries:
                if entry.get("output-file") == output_rel:
                    entry["output-file"] = actual_rel

        except Exception as e:
            result["errors"].append(f"Failed to write {note.get('output_path', '?')}: {e}")

    # Delete or move source files
    attachments_dir = vault / "_attachments"
    for note in notes:
        move_to_attach = note.get("move_to_attachments", False)
        for src in note.get("source_files", []):
            src_path = vault / src if not os.path.isabs(src) else Path(src)
            try:
                # Reject sources that escape the vault before delete/move
                assert_within_vault(src_path, vault)
                if not src_path.exists():
                    continue
                if move_to_attach:
                    # Move to _attachments/ instead of deleting
                    attachments_dir.mkdir(parents=True, exist_ok=True)
                    dest = resolve_collision(attachments_dir / src_path.name)
                    shutil.move(str(src_path), str(dest))
                    result["moved_to_attachments"].append(str(dest.relative_to(vault)))
                    # Move sibling Meeting Recorder companions alongside the source.
                    # They share the same stem and carry canonical metadata/preview the
                    # transcript was generated from, worth keeping with the original.
                    #   - If source is .txt: move sibling .json and .md
                    #   - If source is .md (Case B: MR shipped only .md + .json): move sibling .json
                    src_suffix = src_path.suffix.lower()
                    if src_suffix in (".txt", ".md"):
                        sibling_exts = (".json", ".md") if src_suffix == ".txt" else (".json",)
                        for comp_ext in sibling_exts:
                            comp_path = src_path.with_suffix(comp_ext)
                            if not comp_path.exists():
                                continue
                            try:
                                comp_dest = resolve_collision(attachments_dir / comp_path.name)
                                shutil.move(str(comp_path), str(comp_dest))
                                result["moved_to_attachments"].append(str(comp_dest.relative_to(vault)))
                            except Exception as ce:
                                result["errors"].append(f"Failed to move companion {comp_path.name}: {ce}")
                else:
                    os.remove(src_path)
                    result["deleted"].append(str(src))
            except Exception as e:
                action = "move" if move_to_attach else "delete"
                result["errors"].append(f"Failed to {action} {src}: {e}")

    # Move screenshots into _attachments/screenshots/<stem>/ alongside the transcript.
    # The wikilinks in the body were rewritten to reference this final path during
    # content assembly.
    for note in notes:
        screenshot_files = note.get("screenshot_files") or []
        if not screenshot_files:
            continue
        stem = derive_transcript_stem(note.get("source_files", []))
        if not stem:
            continue  # already warned during content assembly
        target_dir = attachments_dir / "screenshots" / stem
        for src in screenshot_files:
            src_path = vault / src if not os.path.isabs(src) else Path(src)
            try:
                if not src_path.exists():
                    result["errors"].append(f"Screenshot source missing: {src}")
                    continue
                target_dir.mkdir(parents=True, exist_ok=True)
                dest = resolve_collision(target_dir / src_path.name)
                shutil.move(str(src_path), str(dest))
                result["moved_to_attachments"].append(str(dest.relative_to(vault)))
            except Exception as e:
                result["errors"].append(f"Failed to move screenshot {src}: {e}")

    # Delete or move source files for skipped log entries.
    # If the entry sets `move_to_attachments: true` (e.g., duplicate transcripts
    # that the agent wants kept alongside the primary recording), move the file
    # to _attachments/ instead of deleting it. This keeps the file-move contract
    # inside write-notes.py: agents should never move files themselves.
    staging_dir = vault / "00-Inbox" / "_processing"
    for entry in skipped_log_entries:
        source_file = entry.get("source-file", "")
        if not source_file:
            continue
        src_path = staging_dir / source_file
        try:
            if not src_path.exists():
                continue
            if entry.get("move_to_attachments"):
                attachments_dir.mkdir(parents=True, exist_ok=True)
                dest = resolve_collision(attachments_dir / src_path.name)
                shutil.move(str(src_path), str(dest))
                result["moved_to_attachments"].append(str(dest.relative_to(vault)))
            else:
                os.remove(src_path)
                result["skipped_deleted"].append(str(src_path.relative_to(vault)))
        except Exception as e:
            result["errors"].append(f"Failed to handle skipped source {source_file}: {e}")

    # Append log entries (with dedup guard)
    for entry in log_entries + skipped_log_entries:
        if not entry.get("timestamp"):
            entry["timestamp"] = datetime.now().isoformat()
        if not dedup_log_entry(ingest_log, entry):
            ingest_log.append(entry)
            result["logged"] += 1

    # Save ingest log
    if result["logged"] > 0:
        save_ingest_log(vault, ingest_log)

    # Output result
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    print()  # trailing newline


if __name__ == "__main__":
    main()
