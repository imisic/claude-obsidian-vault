#!/usr/bin/env python3
"""Shared utilities for Vault pipeline scripts."""

import json
import os
import random
import re
import string
import sys
from pathlib import Path


def ensure_utf8_stdio():
    """Force UTF-8 on stdin/stdout/stderr on Windows (defaults to a legacy codepage).

    Call at the top of main() in any script that reads/writes JSON via stdio.
    Safe no-op on non-Windows platforms.
    """
    if sys.platform == "win32":
        for stream in ("stdin", "stdout", "stderr"):
            s = getattr(sys, stream, None)
            if s and hasattr(s, "reconfigure"):
                s.reconfigure(encoding="utf-8")

# English reply/forward prefixes. If your org uses non-English mail, add your
# locale's prefixes to the alternation (e.g. r"...|<your-prefix>)...").
REPLY_PREFIXES = re.compile(
    r"^(re|fw|fwd)\s*:\s*", re.IGNORECASE
)


def normalize_subject(subject: str) -> str:
    """Strip reply/forward prefixes, lowercase, trim."""
    normalized = subject.strip()
    while REPLY_PREFIXES.search(normalized):
        normalized = REPLY_PREFIXES.sub("", normalized).strip()
    return normalized.lower()


def recipient_set(items) -> frozenset:
    """Normalize recipient wikilinks/strings to a comparable set.

    Strips [[ ]] wrapping, trims, lowercases, drops empties so a current
    email's recipients compare identically to a stored note's frontmatter
    recipients regardless of formatting.
    """
    out = set()
    for item in items or []:
        s = str(item).strip()
        if s.startswith("[[") and s.endswith("]]"):
            s = s[2:-2]
        s = s.strip().lower()
        if s:
            out.add(s)
    return frozenset(out)


def subject_to_slug(subject: str, max_len: int = 50) -> str:
    """Convert email/meeting subject to a filename slug.

    Strips reply prefixes, lowercases, replaces non-alnum with hyphens,
    collapses runs, trims to max_len.
    """
    text = normalize_subject(subject)
    # Replace non-alphanumeric with hyphens
    slug = re.sub(r"[^a-z0-9]+", "-", text)
    # Collapse multiple hyphens
    slug = re.sub(r"-{2,}", "-", slug)
    # Trim leading/trailing hyphens
    slug = slug.strip("-")
    # Truncate
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug or "untitled"


def guess_wikilink_from_email(email_addr: str) -> str:
    """Best-guess wikilink from an email address when not in lookup.

    E.g., 'John.Doe@acme.example' → '[[John-Doe]]'
    """
    local = email_addr.split("@")[0]
    # Remove .Ext suffix (external contractors)
    local = re.sub(r"\.ext$", "", local, flags=re.IGNORECASE)
    # Split on dots and capitalize
    parts = local.split(".")
    if len(parts) >= 2:
        name = "-".join(p.capitalize() for p in parts[:2])
    else:
        name = parts[0].capitalize()
    return f"[[{name}]]"


def generate_pii_token(prefix: str, existing_tokens: set, length: int = 6) -> str:
    """Generate a unique random token like [EMAIL-a3x9] or [PHONE-b2k7].

    Args:
        prefix: "EMAIL" or "PHONE"
        existing_tokens: set of already-used tokens (from token_to_pii keys)
        length: character length of random part (default 4)

    Returns: token string like "[EMAIL-a3x9]"
    """
    charset = string.ascii_lowercase + string.digits
    for _ in range(100):  # collision guard
        code = "".join(random.choices(charset, k=length))
        token = f"[{prefix}-{code}]"
        if token not in existing_tokens:
            return token
    raise RuntimeError(f"Could not generate unique {prefix} token after 100 attempts")


def atomic_json_write(path: Path, data, indent: int = 2) -> None:
    """Write JSON atomically: write to .tmp, validate, then rename.

    Prevents corruption from partial writes (crash, disk full, encoding error).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)
    # Validate: re-read and check it's valid JSON with non-zero size
    tmp_size = tmp_path.stat().st_size
    if tmp_size == 0:
        tmp_path.unlink(missing_ok=True)
        raise IOError(f"Atomic write failed: {path} tmp file was empty")
    with open(tmp_path, "r", encoding="utf-8") as f:
        json.load(f)  # raises JSONDecodeError if corrupt
    os.replace(str(tmp_path), str(path))


def atomic_text_write(path: Path, content: str) -> None:
    """Write text file atomically: write to .tmp, validate size, then rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    if tmp_path.stat().st_size == 0 and len(content) > 0:
        tmp_path.unlink(missing_ok=True)
        raise IOError(f"Atomic write failed: {path} tmp file was empty")
    os.replace(str(tmp_path), str(path))


def apply_vip_boost(relevance: str, vip_involved: list[str],
                    participant_positions: dict[str, str] | None = None) -> str:
    """Apply VIP relevance boost per vip.md rules.

    Args:
        relevance: current relevance level ("low", "medium", "high")
        vip_involved: list of VIP tier strings (e.g., ["boss-chain", "stakeholder"])
        participant_positions: optional dict mapping VIP tier to position
            ("from_to" or "cc"). If None, assumes "from_to" for all tiers.

    Returns: adjusted relevance string
    """
    if not vip_involved:
        return relevance

    positions = participant_positions or {}
    result = relevance.lower()

    for tier in vip_involved:
        pos = positions.get(tier, "from_to")

        if tier == "boss-chain":
            if pos in ("from_to", "from", "to"):
                if result == "low":
                    result = "medium"
                elif result == "medium":
                    result = "high"
            elif pos == "cc":
                if result == "low":
                    result = "medium"
        elif tier == "stakeholder":
            if pos in ("from_to", "from", "to"):
                if result == "low":
                    result = "medium"
        # team tier: no boost (high-volume daily collab)

    return result


def company_from_domain(email_addr: str) -> str:
    """Infer company from email domain."""
    domain = email_addr.split("@")[-1].lower()
    domain_map = {
        "acme.example": "Acme Corp",
        "acmedigital.example": "Acme Digital",
        "external.acme.example": "Acme Corp",
        "acme-de.example": "Acme Corp",
        "acme-fr.example": "Acme Corp",
        "partner.example": "Partner Co",
    }
    return domain_map.get(domain, "")


# ============================================================
# Task hygiene helpers
# ============================================================

# >>> OWNER CONFIG (single source of truth: edit here or via /w-setup) >>>
# Your wikilink slug, name, company, email addresses, and timezone. Every
# pipeline script imports these from utils; this is the ONLY place to change them.
OWNER_SLUG = "Sam-Rivera"
OWNER_NAME = "Sam Rivera"
OWNER_COMPANY = "Acme Corp"
# Personal / non-work addresses (self-forwards land here):
OWNER_PERSONAL_EMAILS = {
    "sam.rivera@example.com",
}
# Work addresses:
OWNER_WORK_EMAILS = {
    "s.rivera@acme.example",
    "sam.rivera@acme.onmicrosoft.com",
}
# Union, for general "is this the owner?" checks:
OWNER_EMAILS = OWNER_PERSONAL_EMAILS | OWNER_WORK_EMAILS
# IANA timezone for converting calendar timestamps (e.g. "America/New_York", "Europe/Berlin"):
LOCAL_TZ = "America/New_York"
# <<< OWNER CONFIG <<<

PROTECTING_VIP_TIERS = {"boss-chain", "stakeholder"}

_TASK_OPEN_RE = re.compile(r'^(\s*)([-*])\s*\[([ />!])\]\s*(.*)$')
_TASK_DONE_RE = re.compile(r'^(\s*)([-*])\s*\[([xX-])\]\s*(.*)$')
_OWNER_RE = re.compile(r'^\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]\s*(.*)')
_DELEGATED_RE = re.compile(r'\[delegated-by::\s*\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]\]')
_CREATED_RE = re.compile(r'\[created::\s*(\d{4}-\d{2}-\d{2})\]')
_SOURCE_RE = re.compile(r'\[source::\s*\[\[[^\]]+\]\]\]')

# Forgettability filter: distinguishes "real task" from "conversational artifact".
#
# The horizon regex is intentionally broad: it matches phrases like "this is fine"
# or "due diligence" that aren't real time horizons. This is a recall-over-precision
# choice: a false positive keeps a noise task (status quo), a false negative would
# demote a real one (worse). Don't tighten these regexes without re-running the
# backfill audit to confirm the demotion rate doesn't spike.
_FORGETTABILITY_HORIZON_RE = re.compile(
    r'\b(by|before|due|until|after|next|this)\s+\w+'
    r'|\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b'
    r'|\b\d{4}-\d{2}-\d{2}\b'
    r'|\bM[-\s]?\d{1,2}\b',
    re.IGNORECASE,
)
_FORGETTABILITY_NOUN_RE = re.compile(
    r'\b(deck|doc|docs|draft|list|intro|decision|approval|sign[-\s]?off|proposal'
    r'|plan\w*|report|analysis|review|summary|spec|writeup|brief|memo'
    r'|outline|slides|workshop|email|slack|invite|template|sheet|dashboard'
    r'|1on1|1:1|catch[-\s]?up|transition|onboarding|RFP)\b',
    re.IGNORECASE,
)
_FORGETTABILITY_VERB_RE = re.compile(
    r'\b(send|share|forward|ping|ask|check|confirm|verify|dig\s+up'
    r'|find|look\s+up|reach\s+out|follow\s+up|catch\s+up|schedule|set\s+up'
    r'|organize|coordinate|book|invit\w*|introduc\w*|onboard|grant'
    r'|escalate|propos\w*|deliver|prepare|draft|publish|circulate'
    r'|sync|connect|meet|align|decide|present|finalize|review)\b',
    re.IGNORECASE,
)
_FORGETTABILITY_BLOCKER_RE = re.compile(
    r'\b(waiting\s+on|blocked\s+on|once\s+\w+\s+(confirms|approves|signs))\b',
    re.IGNORECASE,
)

# Strip these fields from a task line before forgettability scan so e.g. the
# date inside [created:: 2026-05-13] doesn't satisfy the horizon regex.
_DUE_FIELD_RE = re.compile(r'\[due::\s*[^\]]+\]')


def _task_description_for_forgettability(line: str) -> str:
    """Strip owner wikilink and known metadata fields, return the description text.

    Used to feed `passes_forgettability`, we want to scan what the task is
    *about*, not the surrounding bookkeeping.
    """
    body = line.rstrip("\n")
    m = _TASK_OPEN_RE.match(body)
    if not m:
        return ""
    rest = m.group(4)
    om = _OWNER_RE.match(rest)
    if om:
        rest = om.group(2)
    rest = _SOURCE_RE.sub("", rest)
    rest = _CREATED_RE.sub("", rest)
    rest = _DELEGATED_RE.sub("", rest)
    rest = _DUE_FIELD_RE.sub("", rest)
    return rest.strip()


def passes_forgettability(task_text: str) -> bool:
    """Return True if `task_text` carries any forgettability signal.

    Signals: explicit time horizon, deliverable noun, small-ask verb, blocker.
    A task without any of these is almost certainly a conversational artifact
    (e.g. "read PRD", "attend workshop") that Sam would not actually forget.
    """
    return bool(
        _FORGETTABILITY_HORIZON_RE.search(task_text)
        or _FORGETTABILITY_NOUN_RE.search(task_text)
        or _FORGETTABILITY_VERB_RE.search(task_text)
        or _FORGETTABILITY_BLOCKER_RE.search(task_text)
    )


def _demote_with_marker(line: str, reason: str = "forgettability") -> str:
    """Strip checkbox AND append `[demoted:: <reason>]` so weekly review can find it.

    Input:  `- [ ] [[Owner]] description [created:: ...]`
    Output: `- [[Owner]] description [demoted:: forgettability] [created:: ...]`

    Non-task lines pass through unchanged.
    If `[demoted::]` is already present, no duplicate marker is added.
    """
    has_nl = line.endswith("\n")
    body = line.rstrip("\n")
    m = _TASK_OPEN_RE.match(body)
    if not m:
        # Not a checkbox task, pass through unchanged (plain bullets are already
        # "demoted" by definition; non-bullet text is irrelevant).
        return line
    indent, bullet, _sym, rest = m.group(1), m.group(2), m.group(3), m.group(4)
    new_body = f"{indent}{bullet} {rest}"
    if "[demoted::" not in new_body:
        # Insert marker before [created::] if present, else at end
        cm = _CREATED_RE.search(new_body)
        marker = f"[demoted:: {reason}]"
        if cm:
            new_body = new_body[:cm.start()] + marker + " " + new_body[cm.start():]
        else:
            new_body = new_body.rstrip() + " " + marker
    return new_body + ("\n" if has_nl else "")


def count_audience(frontmatter: dict) -> int:
    """Distinct participants on a note.

    email: len(to) + len(cc). meeting: len(attendees). Missing data: 1.
    """
    ntype = frontmatter.get("type")
    if ntype == "email":
        to = frontmatter.get("to") or []
        cc = frontmatter.get("cc") or []
        return len(to) + len(cc)
    if ntype == "meeting":
        att = frontmatter.get("attendees") or []
        return len(att) if att else 1
    return 1


def has_protecting_vip(frontmatter: dict) -> bool:
    """DEPRECATED: kept only for backwards compatibility with old callers.
    Use load_vip_slugs() + per-task owner check instead.

    Returns True if any boss-chain or stakeholder VIP is in the note's vip-involved
    list. This was the original (too-loose) protection rule: it preserved
    every task in a VIP-attended note, including unrelated third-party commitments
    from group meetings. It has since been superseded by per-task owner-based
    protection.
    """
    tiers = frontmatter.get("vip-involved") or []
    return any(t in PROTECTING_VIP_TIERS for t in tiers)


def load_vip_slugs(vault: Path) -> set:
    """Return wikilink slugs for boss-chain and stakeholder VIPs (the
    'protecting' tiers). Team-tier VIPs are NOT included: they're high-volume
    daily collaborators and protecting all their tasks would re-introduce noise.

    Falls back to empty set if registry is missing or unreadable.
    """
    registry_path = vault / "_db" / "entity-registry.json"
    if not registry_path.exists():
        return set()
    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return set()
    slugs = set()
    for person in data.get("people", []):
        if person.get("vip") in PROTECTING_VIP_TIERS:
            name = person.get("name", "")
            if name:
                slugs.add(name.replace(" ", "-"))
            for alias in person.get("aliases", []) or []:
                slugs.add(alias.replace(" ", "-"))
    return slugs


def parse_task_line(line: str) -> dict:
    """Parse a single line as a possible task.

    Returns dict with keys: is_task, status, owner, delegated_by, has_created.
    """
    result = {
        "is_task": False, "status": None, "owner": "",
        "delegated_by": "", "has_created": False,
    }
    m_open = _TASK_OPEN_RE.match(line.rstrip())
    m_done = _TASK_DONE_RE.match(line.rstrip())
    m = m_open or m_done
    if not m:
        return result

    sym = m.group(3)
    status_map = {
        " ": "todo", "/": "in-progress", ">": "delegated", "!": "urgent",
        "x": "done", "X": "done", "-": "cancelled",
    }
    result["is_task"] = True
    result["status"] = status_map.get(sym)
    rest = m.group(4)

    om = _OWNER_RE.match(rest)
    if om:
        result["owner"] = om.group(1)
        rest_after_owner = om.group(2)
    else:
        rest_after_owner = rest

    dm = _DELEGATED_RE.search(rest_after_owner)
    if dm:
        result["delegated_by"] = dm.group(1)
    result["has_created"] = bool(_CREATED_RE.search(rest_after_owner))
    return result


def stamp_created(line: str, date_str: str) -> str:
    """Append [created:: YYYY-MM-DD] to a task line if not already present.
    Non-task lines pass through unchanged.
    """
    if not date_str:
        return line
    parsed = parse_task_line(line)
    if not parsed["is_task"] or parsed["has_created"]:
        return line

    has_nl = line.endswith("\n")
    body = line.rstrip("\n").rstrip()
    body = f"{body} [created:: {date_str}]"
    return body + ("\n" if has_nl else "")


def _inject_delegated_by(line: str) -> str:
    """Insert [delegated-by:: [[Sam-Rivera]]] before [source::] (or at end)."""
    if _DELEGATED_RE.search(line):
        return line
    has_nl = line.endswith("\n")
    body = line.rstrip("\n")
    sm = _SOURCE_RE.search(body)
    insert = f"[delegated-by:: [[{OWNER_SLUG}]]]"
    if sm:
        body = body[:sm.start()] + insert + " " + body[sm.start():]
    else:
        body = body.rstrip() + " " + insert
    return body + ("\n" if has_nl else "")


def _strip_checkbox(line: str) -> str:
    """Replace `- [ ] [[Owner]] ...` with `- [[Owner]] ...` (plain bullet)."""
    has_nl = line.endswith("\n")
    body = line.rstrip("\n")
    m = _TASK_OPEN_RE.match(body)
    if not m:
        return line
    indent, bullet, _sym, rest = m.group(1), m.group(2), m.group(3), m.group(4)
    body = f"{indent}{bullet} {rest}"
    return body + ("\n" if has_nl else "")


def apply_task_hygiene(line: str, frontmatter: dict, vip_slugs: set | None = None) -> str:
    """Apply the hygiene matrix to a single line.

    Order of operations:
    1. Non-task lines pass through.
    2. Done/cancelled tasks: just stamp [created::].
    3. Stamp [created::] on all surviving open tasks.
    4. If task already has [delegated-by::]: preserve (it's an explicit ask).
    5. If owner is Sam AND no delegated-by AND task
       description fails the forgettability test → demote to plain bullet with
       [demoted:: forgettability] marker.
    6. If owner is a boss-chain/stakeholder VIP: preserve (per-task protection).
    7. Size-based matrix for non-Sam, non-VIP, non-delegated owners:
       - 1on1 / sent email / 2-5 attendees: inject [delegated-by:: [[Sam-Rivera]]]
       - >5 attendees / >5 To+CC broadcast: strip checkbox to plain bullet
       - received email <=5 To+CC: keep as-is (owed-to-Sam ambiguity)
    """
    parsed = parse_task_line(line)
    date_str = frontmatter.get("date", "")

    if not parsed["is_task"]:
        return line

    if parsed["status"] in ("done", "cancelled"):
        return stamp_created(line, date_str)

    line = stamp_created(line, date_str)

    # Already-delegated tasks: explicit ask, preserve regardless of owner.
    if parsed["delegated_by"]:
        return line

    # NEW: Sam-owned tasks face the forgettability filter.
    if parsed["owner"] == OWNER_SLUG:
        desc = _task_description_for_forgettability(line)
        if not passes_forgettability(desc):
            return _demote_with_marker(line, "forgettability")
        return line

    # Per-task VIP protection (a VIP just attending doesn't protect others).
    if vip_slugs and parsed["owner"] in vip_slugs:
        return line

    audience = count_audience(frontmatter)
    ntype = frontmatter.get("type")
    direction = frontmatter.get("direction", "")

    # Size dominates classifier labels. A note marked meeting-type=1on1 but with
    # 9 attendees (a classifier miss, happens when the `person:` field is set
    # on a multi-person sync) should be treated as the large meeting it is.

    if ntype == "email":
        if direction == "sent":
            return _inject_delegated_by(line)
        if audience > 5:
            return _strip_checkbox(line)
        return line  # received small: keep (owed-to-Sam ambiguity)

    if ntype == "meeting":
        if audience > 5:
            return _strip_checkbox(line)
        return _inject_delegated_by(line)

    return line
