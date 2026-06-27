#!/usr/bin/env python3
"""
validate-notes.py: Post-processing validation for created interaction notes.

Checks required frontmatter fields, summary presence, and wikilink format.
Returns JSON with pass/fail per note and issues found.

Usage:
    python3 validate-notes.py note1.md note2.md ...
    python3 validate-notes.py --from-stdin  (reads JSON array of paths from stdin)
"""

import json
import re
import sys
from pathlib import Path

from utils import ensure_utf8_stdio


# Required fields by note type. Keep non-interaction schemas intentionally
# light: this validator is used after ingestion, but should also be useful for
# vault-wide audits without treating projects/people like emails.
REQUIRED_FIELDS = {
    "email": ["date", "type", "interaction-type", "from", "to", "subject", "relevance", "summary", "source-file"],
    "meeting": ["date", "type", "interaction-type", "meeting-type", "summary", "source-file"],
    "reference": ["date", "type", "source-file"],
    "project": ["type", "status"],
    "workstream": ["type", "status", "parent-project"],
    "person": ["type", "status"],
}

# Base required for any interaction note
BASE_REQUIRED = ["date", "type", "interaction-type", "summary", "source-file"]

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# Body-level lint patterns: raw @mention (should be a wikilink), leaked Dataview
# (should be stripped from processed notes), and un-tokenized PII (email bodies
# are sanitized to [EMAIL-xxxx]/[PHONE-xxxx], so a raw one is a leak).
AT_MENTION_RE = re.compile(r"(?<![\w@/])@([A-Z][a-z]+)")
DATAVIEW_BLOCK_RE = re.compile(r"```\s*dataview")
DATAVIEW_INLINE_RE = re.compile(r"`\$?=")
RAW_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
RAW_PHONE_RE = re.compile(r"(?<!\w)\+\d[\d ()\-]{7,}\d")


def extract_frontmatter(content: str) -> dict | None:
    """Extract YAML frontmatter as a dict (simple key: value parsing)."""
    if not content.startswith("---"):
        return None

    end = content.find("---", 3)
    if end < 0:
        return None

    fm_text = content[3:end]
    result = {}
    current_key = None
    current_list = None

    for line in fm_text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # List item
        if stripped.startswith("- ") and current_key:
            if current_list is None:
                current_list = []
                result[current_key] = current_list
            current_list.append(stripped[2:].strip().strip('"'))
            continue

        # Key: value
        colon = stripped.find(":")
        if colon > 0:
            key = stripped[:colon].strip()
            val = stripped[colon + 1:].strip().strip('"')
            current_key = key
            current_list = None
            if val:
                result[key] = val
            else:
                result[key] = None  # Key exists but empty value

    return result


def extract_body(content: str) -> str:
    """Return the note body: everything after the closing frontmatter fence."""
    if not content.startswith("---"):
        return content
    end = content.find("---", 3)
    if end < 0:
        return ""
    return content[end + 3:]


def validate_note(filepath: str) -> dict:
    """Validate a single note file. Returns {path, valid, issues}."""
    path = Path(filepath)
    issues = []

    if not path.exists():
        return {"path": filepath, "valid": False, "issues": ["File does not exist"]}

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"path": filepath, "valid": False, "issues": [f"Cannot read: {e}"]}

    fm = extract_frontmatter(content)
    if fm is None:
        return {"path": filepath, "valid": False, "issues": ["No YAML frontmatter found"]}

    # Determine note type
    note_type = fm.get("type", "")
    interaction_type = fm.get("interaction-type", "")

    if interaction_type == "email":
        required = REQUIRED_FIELDS["email"]
    elif interaction_type == "meeting":
        required = REQUIRED_FIELDS["meeting"]
    elif note_type == "reference":
        required = REQUIRED_FIELDS["reference"]
    elif note_type == "project":
        required = REQUIRED_FIELDS["project"]
    elif note_type == "workstream":
        required = REQUIRED_FIELDS["workstream"]
    elif note_type == "person":
        required = REQUIRED_FIELDS["person"]
    elif note_type:
        required = ["type"]
    else:
        required = BASE_REQUIRED

    # For sent emails, 'to' is optional (can be empty or missing)
    is_sent_email = interaction_type == "email" and fm.get("direction") == "sent"

    # Check required fields
    for field in required:
        if is_sent_email and field == "to":
            continue  # 'to' is optional for sent emails
        if field not in fm:
            issues.append(f"Missing required field: {field}")
        elif fm[field] is None or (isinstance(fm[field], str) and not fm[field].strip()):
            issues.append(f"Empty required field: {field}")

    # Check summary is not a placeholder
    summary = fm.get("summary", "")
    if isinstance(summary, str) and summary.strip():
        if len(summary.strip()) < 10:
            issues.append(f"Summary too short ({len(summary.strip())} chars)")
        if "[[" in summary:
            issues.append("Summary contains wikilinks (should be plain text)")

    # Check meeting-type values
    if interaction_type == "meeting":
        mt = fm.get("meeting-type", "")
        valid_types = {"general", "1on1", "steerco", "sync"}
        if mt and mt not in valid_types:
            issues.append(f"Invalid meeting-type: '{mt}' (expected one of {valid_types})")

    # Check email relevance values
    if interaction_type == "email":
        rel = fm.get("relevance", "")
        if rel and rel not in {"high", "medium", "low"}:
            issues.append(f"Invalid relevance: '{rel}'")

    if note_type == "project":
        if not any(fm.get(k) for k in ("lead", "owner", "product-lead", "business-lead", "engineering-lead")):
            issues.append("Project missing owner/lead field")

    # Body-level checks for interaction notes (reference/project/person bodies are
    # not entity-resolved or PII-sanitized, so skip them).
    if interaction_type in ("email", "meeting") or note_type == "async":
        body = extract_body(content)
        mentions = sorted({m.group(1) for m in AT_MENTION_RE.finditer(body)})
        if mentions:
            shown = ", ".join("@" + m for m in mentions[:5])
            issues.append(f"Unresolved @mention(s) in body (convert to [[wikilink]]): {shown}")
        if DATAVIEW_BLOCK_RE.search(body) or DATAVIEW_INLINE_RE.search(body):
            issues.append("Dataview query left in processed note (should be stripped)")
        # PII tokenization is applied to email bodies only; a raw address there is a leak.
        if interaction_type == "email":
            if RAW_EMAIL_RE.search(body):
                issues.append("Un-tokenized email address in body (PII should be [EMAIL-xxxx])")
            if RAW_PHONE_RE.search(body):
                issues.append("Un-tokenized phone number in body (PII should be [PHONE-xxxx])")

    return {
        "path": filepath,
        "valid": len(issues) == 0,
        "issues": issues,
    }


def main():
    ensure_utf8_stdio()
    if "--from-stdin" in sys.argv:
        paths = json.load(sys.stdin)
    else:
        paths = sys.argv[1:]

    if not paths:
        print("Usage: validate-notes.py [--from-stdin] [file1.md file2.md ...]", file=sys.stderr)
        sys.exit(1)

    results = [validate_note(p) for p in paths]

    passed = sum(1 for r in results if r["valid"])
    failed = sum(1 for r in results if not r["valid"])

    output = {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "results": results,
    }

    json.dump(output, sys.stdout, indent=2)
    print()

    if failed > 0:
        print(f"\n{failed}/{len(results)} notes have validation issues:", file=sys.stderr)
        for r in results:
            if not r["valid"]:
                print(f"  {r['path']}: {', '.join(r['issues'])}", file=sys.stderr)


if __name__ == "__main__":
    main()
