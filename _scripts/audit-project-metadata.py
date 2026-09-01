#!/usr/bin/env python3
"""
audit-project-metadata.py: deterministic resolver + auditor for the `project:`
frontmatter field on interaction notes.

WHY this exists: `_db/entity-registry.json` holds people, not projects, so there
is nothing to validate `project:` against and the links drift. A note gets filed
under a project that was later renamed, or under no project at all, and neither
`vault-health.py` (which checks wikilink targets) nor `audit-link-casing.py`
(which checks casing) notices, because both treat `project:` as ordinary
frontmatter text.

Governing principle, from CLAUDE.md: false positives are worse than omissions.
Anything the resolver is not sure about is FLAGGED, never auto-changed.

Two levels of usefulness:

  * **With no configuration at all**, this validates link integrity: every
    `project:` value must point at a real `03-Projects/*.md` note. That alone
    catches renames and typos, and it is why the script is worth running on a
    fresh vault.
  * **Once you fill in the signal config below**, it also backfills missing
    tags and flags notes filed under the wrong project.

Modes:
  (default)  report: counts + per-category file lists
  --fix      apply ONLY safe categories: MISMATCH, MISSING-HIGH-CONFIDENCE,
             EMPTY/MALFORMED. UNCERTAIN and UNKNOWN-TARGET are never applied.
  --json     machine-readable output

Never creates a project note. Never edits outside `05-Interactions/`.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import ensure_utf8_stdio  # noqa: E402

VAULT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = VAULT / "03-Projects"
INTERACTIONS_DIR = VAULT / "05-Interactions"

# >>> PROJECT SIGNAL CONFIG (edit here) >>>
#
# Every list below ships EMPTY. Empty means "no opinion": the resolver can never
# reach high confidence, so the audit reports link integrity only and `--fix`
# has nothing unsafe it could do. Fill these in to unlock backfill.
#
# The model is signal DOMINANCE, not keyword presence. A note that mentions a
# guard term once still resolves to your domain when anchors outnumber guards.
# That distinction is the whole reason this is not a keyword matcher.
#
# ONE RULE WORTH KEEPING WHATEVER YOU PUT HERE: match the topic phrase, never a
# person's name on its own. Someone who owns a project attends many meetings
# that are not about it, so a bare name as a trigger tags all of them.

# Patterns that positively identify your vault's primary domain. The count of
# DISTINCT patterns that match is the note's anchor score.
#   e.g. [r"\bonboarding\b", r"\bpartner\s*agreement\b"]
ANCHORS: list[str] = []

# Unambiguous markers of an adjacent or previous domain that is NOT what this
# vault tracks (an old role, a wound-down programme). Suppresses a match when
# guard signal is at least as strong as the anchor signal.
#   e.g. [r"\blegacy\s*billing\b"]
HARD_GUARDS: list[str] = []

# Words that belong to that other domain but collide with your primary
# vocabulary. Too weak to veto on their own: they only suppress when the note
# has no anchor at all.
SOFT_GUARDS: list[str] = []

# One signature per top-level area you track. A note matching CROSS_CUTTING_MIN
# or more of these covers several areas at once (a portfolio review, a steerco
# spanning workstreams) and is not one project, so it is always flagged for a
# manual call rather than forced onto whichever matched first.
CROSS_CUTTING_SIGNALS: list[str] = []
CROSS_CUTTING_MIN = 3

# Projects OUTSIDE the primary domain, matched only when no anchor is present.
# Maps a `03-Projects/` slug to its patterns and the confidence a match earns.
# Use "uncertain" unless the phrase is unmistakable.
#   e.g. {"Partner-Onboarding": ([r"\bpartner\s*onboarding\b"], "high")}
SECONDARY_PROJECTS: dict[str, tuple[list[str], str]] = {}

# Sub-project refinement, checked only when an anchor is present. Maps a parent
# slug to its children. Exactly one child matching wins; several matching means
# the note spans them, so it stays on the parent.
#   e.g. {"Project-Alpha": {"Project-Alpha-Pilot": [r"\bpilot\b"]}}
SUBPROJECT_KEYWORDS: dict[str, dict[str, list[str]]] = {}

# The parent slug that a bare anchor (no sub-project match) resolves to. Leave
# as None if your domain has no single umbrella project.
PRIMARY_PARENT: str | None = None

# How many distinct anchors a note needs before the resolver will BACKFILL a
# missing `project:` line. Below this it is flagged UNCERTAIN instead.
BACKFILL_MIN_ANCHORS = 3

# Genuine SYNONYMS of a canonical project, safe to auto-retag: an old name for
# the same thing. Keys are the out-of-scope slug found in the wild.
#
# Do NOT put distinct organizing concepts here. A tag that means something real
# but different is not drift, and retagging it silently destroys information.
# Those get flagged UNCERTAIN so you decide.
#   e.g. {"Alpha-Programme": "Project-Alpha"}
DRIFT_ALIASES: dict[str, str] = {}
# <<< PROJECT SIGNAL CONFIG <<<

PROJECT_RE = re.compile(r"^project:\s*(.*?)\s*$", re.MULTILINE)
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def valid_project_targets() -> set[str]:
    """Derived from the filesystem so the list cannot rot."""
    return {p.stem for p in PROJECTS_DIR.glob("*.md")}


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_text, body). frontmatter_text is '' when absent."""
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    if end == -1:
        return "", text
    return text[: end + 4], text[end + 4 :]


def get_project_value(fm_text: str) -> str | None:
    """The raw `project:` value, or None when there is no such line."""
    m = PROJECT_RE.search(fm_text)
    return m.group(1) if m else None


def project_slug(raw: str | None) -> str | None:
    """The [[Slug]] target inside a project value, or None."""
    if not raw:
        return None
    m = WIKILINK_RE.search(raw)
    return m.group(1) if m else None


def any_match(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def count_hits(patterns: list[str], text: str) -> int:
    """How many DISTINCT patterns match. Repeats of one pattern count once."""
    return sum(1 for p in patterns if re.search(p, text))


def resolve(text: str) -> tuple[str | None, str, str]:
    """
    Deterministic resolver. Returns (slug_or_None, confidence, reason) where
    confidence is one of "high", "uncertain", "none".

    A None slug at "high" confidence is a positive finding: the note genuinely
    belongs to no tracked project. A None slug at "none" means no signal either
    way, which is the answer on an unconfigured vault.
    """
    low = text.lower()
    anchors = count_hits(ANCHORS, low)
    hard = count_hits(HARD_GUARDS, low)
    soft = count_hits(SOFT_GUARDS, low)

    # Hard guards veto unless the primary domain clearly outweighs them. Checked
    # first because nothing legitimately co-opts an unambiguous marker.
    if hard > 0 and anchors <= hard:
        return None, "high", f"out-of-domain signal dominates (anchors={anchors}, hard={hard})"

    # Spans several areas: a portfolio sync, not one project.
    verticals = count_hits(CROSS_CUTTING_SIGNALS, low)
    if verticals >= CROSS_CUTTING_MIN:
        return None, "uncertain", f"cross-cutting note ({verticals} areas), manual call"

    # Secondary projects are checked BEFORE soft-guard suppression, because a
    # legitimate secondary project often reuses the vocabulary soft guards flag.
    if anchors == 0:
        for slug, (patterns, conf) in SECONDARY_PROJECTS.items():
            if any_match(patterns, low):
                return slug, conf, f"{slug} keyword, no primary anchor"

    # Soft guards only matter when there is no primary anchor at all.
    if soft > 0 and anchors == 0:
        return None, "high", f"out-of-domain vocabulary, no anchor (soft={soft})"

    if anchors > 0:
        conf = "high" if anchors >= BACKFILL_MIN_ANCHORS else "uncertain"
        density = f"anchors={anchors}"
        children = SUBPROJECT_KEYWORDS.get(PRIMARY_PARENT or "", {})
        hits = [slug for slug, pats in children.items() if any_match(pats, low)]
        if len(hits) == 1:
            return hits[0], conf, f"anchor + single sub-project {hits[0]} ({density})"
        if len(hits) > 1:
            return PRIMARY_PARENT, conf, f"anchor + several sub-projects {hits} -> parent ({density})"
        return PRIMARY_PARENT, conf, f"anchor, no sub-project -> parent ({density})"

    return None, "none", "no project signal"


def _same_family(a: str, b: str) -> bool:
    """True when one slug is the parent of the other under SUBPROJECT_KEYWORDS."""
    for parent, children in SUBPROJECT_KEYWORDS.items():
        if a == parent and b in children:
            return True
        if b == parent and a in children:
            return True
    return False


def classify(path: Path, valid_targets: set[str]) -> dict:
    """Category and proposed action for one note."""
    text = path.read_text(encoding="utf-8")
    fm, _ = split_frontmatter(text)
    raw = get_project_value(fm)
    current = project_slug(raw)
    resolved, conf, reason = resolve(text)

    result = {
        "path": path,
        "current": current,
        "has_line": raw is not None,
        "resolved": resolved,
        "conf": conf,
        "reason": reason,
        "category": "OK",
        "action": None,          # 'remove' | ('set', slug) | ('add', slug) | None
    }

    # Line present but blank or carrying no [[...]] target.
    if result["has_line"] and current is None:
        result["category"] = "EMPTY/MALFORMED"
        if resolved and conf == "high":
            result["action"] = ("set", resolved)
            result["reason"] = f"empty -> {resolved} ({reason})"
        else:
            result["action"] = "remove"
            result["reason"] = f"empty, no high-confidence project ({reason})"
        return result

    if current:
        in_scope = current in valid_targets

        # Points at a project note that does not exist. Report only: the fix may
        # well be to create that note, which this script must never do.
        if not in_scope and current not in DRIFT_ALIASES:
            result["category"] = "UNKNOWN-TARGET"
            result["reason"] = f"[[{current}]] is not a note in 03-Projects/"
            return result

        # Resolver is confident the note belongs to no tracked project.
        if resolved is None and conf == "high":
            if in_scope:
                result["category"] = "MISMATCH"
                result["action"] = "remove"
            return result

        # A known synonym of the slug the content points at: safe to retag.
        if not in_scope and DRIFT_ALIASES.get(current) == resolved and resolved in valid_targets:
            result["category"] = "MISMATCH"
            result["action"] = ("set", resolved)
            return result

        # Both canonical but they disagree. Parent/child refinement inside one
        # family is a finer tag, not a contradiction, so it stays OK.
        if (resolved and in_scope and resolved in valid_targets
                and resolved != current and conf == "high"
                and not _same_family(current, resolved)):
            result["category"] = "MISMATCH"
            result["action"] = ("set", resolved)
        return result

    # No project line at all.
    if resolved and conf == "high":
        if resolved in valid_targets:
            result["category"] = "MISSING-HIGH-CONFIDENCE"
            result["action"] = ("add", resolved)
        else:
            result["category"] = "UNCERTAIN"
            result["reason"] = f"resolved [[{resolved}]] is not a note in 03-Projects/"
        return result
    if conf == "uncertain":
        result["category"] = "UNCERTAIN"
    return result


def apply_action(path: Path, result: dict) -> bool:
    """Surgically add, replace or remove the `project:` line. Nothing else."""
    action = result["action"]
    if not action:
        return False

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    proj_idx = next((i for i, ln in enumerate(lines) if re.match(r"^project:", ln)), None)

    if action == "remove":
        if proj_idx is None:
            return False
        del lines[proj_idx]
    elif action[0] == "set":
        if proj_idx is None:
            return False
        nl = "\n" if lines[proj_idx].endswith("\n") else ""
        lines[proj_idx] = f'project: "[[{action[1]}]]"' + nl
    elif action[0] == "add":
        # Insert before the closing '---' of the frontmatter.
        dashes = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
        if len(dashes) < 2:
            return False
        lines.insert(dashes[1], f'project: "[[{action[1]}]]"\n')
    else:
        return False

    path.write_text("".join(lines), encoding="utf-8")
    return True


SAFE_TO_FIX = {"MISMATCH", "MISSING-HIGH-CONFIDENCE", "EMPTY/MALFORMED"}


def main() -> int:
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description="Audit `project:` frontmatter on interaction notes")
    ap.add_argument("--fix", action="store_true",
                    help="apply safe categories only; UNCERTAIN is never touched")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--vault", type=Path, default=VAULT)
    args = ap.parse_args()

    projects_dir = args.vault / "03-Projects"
    interactions = args.vault / "05-Interactions"
    if not projects_dir.is_dir():
        print(f"no 03-Projects/ under {args.vault}", file=sys.stderr)
        return 2

    valid = {p.stem for p in projects_dir.glob("*.md")}
    results = [classify(p, valid) for p in sorted(interactions.rglob("*.md"))]

    applied = 0
    if args.fix:
        for r in results:
            if r["category"] in SAFE_TO_FIX and apply_action(r["path"], r):
                applied += 1

    buckets: dict[str, list[dict]] = {}
    for r in results:
        buckets.setdefault(r["category"], []).append(r)

    if args.json:
        print(json.dumps({
            "scanned": len(results),
            "applied": applied,
            "configured": bool(ANCHORS or SECONDARY_PROJECTS),
            "categories": {
                cat: [{"file": str(r["path"].relative_to(args.vault)),
                       "current": r["current"], "resolved": r["resolved"],
                       "reason": r["reason"]} for r in rs]
                for cat, rs in buckets.items()
            },
        }, indent=2))
        return 0

    print(f"Scanned {len(results)} interaction note(s) against {len(valid)} project(s).")
    if not (ANCHORS or SECONDARY_PROJECTS):
        print("Signal config is empty: reporting link integrity only. "
              "Fill in PROJECT SIGNAL CONFIG to enable backfill.")
    for cat in ("UNKNOWN-TARGET", "MISMATCH", "MISSING-HIGH-CONFIDENCE",
                "EMPTY/MALFORMED", "UNCERTAIN", "OK"):
        rs = buckets.get(cat)
        if not rs:
            continue
        print(f"\n{cat}: {len(rs)}")
        if cat == "OK":
            continue
        for r in rs:
            print(f"  {r['path'].relative_to(args.vault)}")
            print(f"    {r['reason']}")
    if args.fix:
        print(f"\nApplied {applied} change(s). UNCERTAIN and UNKNOWN-TARGET left alone.")
    elif any(buckets.get(c) for c in SAFE_TO_FIX):
        print("\nRe-run with --fix to apply the safe categories.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
