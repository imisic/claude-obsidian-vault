#!/usr/bin/env python3
"""
prep-1on1-data.py: Extract all data needed for 1on1 prep into one compact JSON.

Reads person-index.json and open-actions.json, filters to the target person,
and outputs a single JSON with everything the 1on1-prep agent needs.

Usage:
    python3 prep-1on1-data.py --person "Vikram-Rao" [--vault PATH] [--days 21]

Output: JSON to stdout (small enough for agent context)
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from utils import ensure_utf8_stdio, OWNER_SLUG


def find_person(person_index: dict, query: str) -> str | None:
    """Find person slug by exact or partial match."""
    query_lower = query.lower().replace(" ", "-")

    # Exact match
    if query_lower in {k.lower() for k in person_index.get("meta", {})}:
        for k in person_index["meta"]:
            if k.lower() == query_lower:
                return k

    # Partial match (first name)
    matches = []
    for k in person_index.get("meta", {}):
        if k.lower().startswith(query_lower):
            matches.append(k)
    if len(matches) == 1:
        return matches[0]

    # Fuzzy: query appears anywhere in the slug
    for k in person_index.get("meta", {}):
        if query_lower in k.lower():
            matches.append(k)
    if len(matches) == 1:
        return matches[0]

    return None


def main():
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Extract 1on1 prep data for a person")
    parser.add_argument("--person", required=True, help="Person slug or partial name")
    parser.add_argument("--vault", default=str(Path(__file__).resolve().parent.parent),
                        help="Vault root directory")
    parser.add_argument("--days", type=int, default=21,
                        help="How many days back to include interactions (default: 21)")
    args = parser.parse_args()

    vault = Path(args.vault)
    person_index_path = vault / "_db" / "person-index.json"
    open_actions_path = vault / "_db" / "open-actions.json"

    if not person_index_path.exists():
        print("Error: _db/person-index.json not found. Run build-person-index.py first.", file=sys.stderr)
        sys.exit(1)
    if not open_actions_path.exists():
        print("Error: _db/open-actions.json not found. Run build-open-actions.py first.", file=sys.stderr)
        sys.exit(1)

    with open(person_index_path, "r", encoding="utf-8") as f:
        person_index = json.load(f)
    with open(open_actions_path, "r", encoding="utf-8") as f:
        open_actions = json.load(f)

    # Resolve person
    person_slug = find_person(person_index, args.person)
    if not person_slug:
        print(f"Error: Could not find person matching '{args.person}'", file=sys.stderr)
        print(f"Available people: {', '.join(sorted(person_index.get('meta', {}).keys())[:20])}...",
              file=sys.stderr)
        sys.exit(1)

    print(f"Resolved: {person_slug}", file=sys.stderr)

    meta = person_index.get("meta", {}).get(person_slug, {})

    # Differential windowing: use last 1on1 date as cutoff if available
    last_1on1_date = meta.get("last_1on1", "")
    if last_1on1_date:
        cutoff = last_1on1_date
        print(f"Using last 1on1 date as cutoff: {cutoff}", file=sys.stderr)
    else:
        cutoff = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
        print(f"No prior 1on1 found, using --days {args.days} fallback: {cutoff}", file=sys.stderr)

    # Get person's interactions
    all_interactions = person_index.get("interactions", {}).get(person_slug, [])

    # Split: recent (within --days) vs older 1on1s (last 3 for history)
    recent = [i for i in all_interactions if i.get("date", "") >= cutoff]
    past_1on1s = [i for i in all_interactions
                  if i.get("meeting_type") == "1on1" and i.get("date", "") < cutoff][:3]

    # Get open actions related to this person
    person_actions = open_actions.get("by_person", {}).get(person_slug, [])
    # Also get actions owned by Sam that mention this person
    owner_actions = [a for a in open_actions.get("by_owner", {}).get(OWNER_SLUG, [])
                    if person_slug in a.get("mentioned", []) and a not in person_actions]

    # Carry-forward items from last 1on1's "Next time" section
    carry_forward = meta.get("next_time_items", [])

    output = {
        "person": person_slug,
        "meta": meta,
        "recent_interactions": recent,
        "past_1on1s": past_1on1s,
        "open_actions": {
            "owned_by_person": [a for a in person_actions if a.get("owner") == person_slug],
            "involving_person": [a for a in person_actions if a.get("owner") != person_slug],
            "owner_actions_mentioning": owner_actions,
        },
        "carry_forward": carry_forward,
        "cutoff_date": cutoff,
    }

    json.dump(output, sys.stdout, indent=2, ensure_ascii=False)
    print(file=sys.stderr)
    print(f"Output: {len(recent)} recent interactions, {len(past_1on1s)} past 1on1s, "
          f"{len(person_actions)} actions", file=sys.stderr)


if __name__ == "__main__":
    main()
