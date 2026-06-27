#!/usr/bin/env python3
"""
build-person-index.py: Build person→interactions index from interaction notes.

Scans 05-Interactions/**/*.md frontmatter for participant fields (from, to, cc,
attendees, person). Builds a lookup: person_slug → list of interactions with
metadata (date, type, summary, role). Avoids full-body reads, frontmatter only.

Usage:
    python3 build-person-index.py [--vault PATH]

Output: _db/person-index.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import atomic_json_write, OWNER_SLUG


# Match [[Person-Name]] wikilinks
WIKILINK_RE = re.compile(r'\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]')


def extract_frontmatter(filepath: Path) -> dict | None:
    """Extract YAML frontmatter fields relevant to person indexing."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(4000)
    except Exception:
        return None

    if not content.startswith("---"):
        return None

    fm_end = content.find("---", 3)
    if fm_end < 0:
        return None

    fm_block = content[3:fm_end]
    result = {}
    current_list_key = None
    current_list = []

    for line in fm_block.split("\n"):
        stripped = line.strip()

        # Detect list continuation (indented "- value")
        if current_list_key and stripped.startswith("- "):
            val = stripped[2:].strip().strip('"').strip("'")
            if val:
                current_list.append(val)
            continue
        elif current_list_key:
            # End of list
            result[current_list_key] = current_list
            current_list_key = None
            current_list = []

        if ":" not in stripped:
            continue

        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")

        # Fields we care about
        if key in ("date", "type", "interaction-type", "meeting-type",
                    "summary", "subject", "direction", "relevance", "person"):
            if val:
                result[key] = val
        elif key in ("from",):
            if val:
                result[key] = val
        elif key in ("to", "cc", "attendees", "vip-involved", "email-thread"):
            if val and val != "[]":
                # Inline list: [a, b, c]
                if val.startswith("["):
                    items = [v.strip().strip('"').strip("'") for v in val[1:-1].split(",")]
                    result[key] = [i for i in items if i]
                else:
                    # Single value on same line
                    result[key] = [val]
            else:
                # Might be a multi-line list
                current_list_key = key
                current_list = []

    # Flush any remaining list
    if current_list_key:
        result[current_list_key] = current_list

    return result if result else None


def extract_people_from_field(value) -> list[str]:
    """Extract person slugs from a frontmatter field (string or list)."""
    people = []
    if isinstance(value, str):
        for match in WIKILINK_RE.findall(value):
            people.append(match)
    elif isinstance(value, list):
        for item in value:
            for match in WIKILINK_RE.findall(str(item)):
                people.append(match)
    return people


def main():
    parser = argparse.ArgumentParser(description="Build person→interactions index")
    parser.add_argument("--vault", default=str(Path(__file__).resolve().parent.parent),
                        help="Vault root directory")
    parser.add_argument("--skip-if-recent", type=int, default=0, metavar="SECONDS",
                        help="Skip rebuild if output file is younger than SECONDS")
    args = parser.parse_args()

    vault = Path(args.vault)
    interactions_dir = vault / "05-Interactions"
    output_path = vault / "_db" / "person-index.json"

    if args.skip_if_recent > 0 and output_path.exists():
        import time
        age = time.time() - output_path.stat().st_mtime
        if age < args.skip_if_recent:
            print(f"person-index.json is {int(age)}s old (< {args.skip_if_recent}s), skipping",
                  file=sys.stderr)
            sys.exit(0)

    # person_slug → list of interaction entries
    index: dict[str, list[dict]] = {}
    total_scanned = 0
    total_indexed = 0

    if not interactions_dir.exists():
        print("Error: 05-Interactions/ not found", file=sys.stderr)
        sys.exit(1)

    for md_file in sorted(interactions_dir.rglob("*.md")):
        total_scanned += 1
        fm = extract_frontmatter(md_file)
        if not fm:
            continue

        note_type = fm.get("type", "")
        if note_type not in ("email", "meeting"):
            continue

        rel_path = str(md_file.relative_to(vault))
        date = fm.get("date", "")
        summary = fm.get("summary", "") or fm.get("subject", "")

        base_entry = {
            "path": rel_path,
            "date": date,
            "type": note_type,
            "interaction_type": fm.get("interaction-type", ""),
            "meeting_type": fm.get("meeting-type", ""),
            "summary": summary,
            "direction": fm.get("direction", ""),
            "relevance": fm.get("relevance", ""),
            "vip_involved": fm.get("vip-involved", []),
        }

        # Collect person→role mappings
        person_roles: dict[str, str] = {}  # slug → role

        # from field
        for p in extract_people_from_field(fm.get("from", "")):
            person_roles[p] = "from"

        # to field
        for p in extract_people_from_field(fm.get("to", [])):
            if p not in person_roles:
                person_roles[p] = "to"

        # cc field
        for p in extract_people_from_field(fm.get("cc", [])):
            if p not in person_roles:
                person_roles[p] = "cc"

        # attendees field (meetings)
        for p in extract_people_from_field(fm.get("attendees", [])):
            if p not in person_roles:
                person_roles[p] = "attendee"

        # person field (1on1s)
        for p in extract_people_from_field(fm.get("person", "")):
            if p not in person_roles:
                person_roles[p] = "person"

        # Skip Sam, he's in everything
        person_roles.pop(OWNER_SLUG, None)

        if not person_roles:
            continue

        total_indexed += 1

        for person_slug, role in person_roles.items():
            entry = {**base_entry, "role": role}
            index.setdefault(person_slug, []).append(entry)

    # Sort each person's interactions by date descending
    for person_slug in index:
        index[person_slug].sort(key=lambda e: e["date"], reverse=True)

    # Build summary stats per person
    meta: dict[str, dict] = {}
    for person_slug, entries in index.items():
        one_on_ones = [e for e in entries if e.get("meeting_type") == "1on1"]
        meta[person_slug] = {
            "total_interactions": len(entries),
            "last_interaction": entries[0]["date"] if entries else "",
            "last_1on1": one_on_ones[0]["date"] if one_on_ones else "",
            "last_1on1_path": one_on_ones[0]["path"] if one_on_ones else "",
        }

    # Extract "Next time" items from the most recent 1on1 per person
    for person_slug, m in meta.items():
        last_1on1_path = m.get("last_1on1_path", "")
        if not last_1on1_path:
            continue
        full_path = vault / last_1on1_path
        if not full_path.exists():
            continue
        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # Find ## Next time section
        nt_match = re.search(r'^## Next time\b.*$', content, re.MULTILINE)
        if not nt_match:
            continue
        after = content[nt_match.end():]
        # Collect lines until next ## heading or EOF
        items = []
        for line in after.split("\n"):
            stripped = line.strip()
            if stripped.startswith("## "):
                break
            if stripped.startswith("- ") and len(stripped) > 2:
                items.append(stripped[2:].strip())
        if items:
            m["next_time_items"] = items

    output = {
        "meta": meta,
        "interactions": index,
    }

    atomic_json_write(output_path, output)

    print(f"Scanned {total_scanned} notes, indexed {total_indexed} with participants, "
          f"{len(index)} unique people", file=sys.stderr)


if __name__ == "__main__":
    main()
