#!/usr/bin/env python3
"""
build-email-lookup.py: Extract email→wikilink+VIP lookup from entity-registry.json.

Produces a lightweight JSON (~4KB) for inline email processing, avoiding the need
to load the full entity registry in the main context.

Usage:
    python3 build-email-lookup.py [--vault PATH]

Output: _db/email-lookup.json
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import atomic_json_write


def main():
    parser = argparse.ArgumentParser(description="Build email→wikilink lookup from registry")
    parser.add_argument("--vault", default=str(Path(__file__).resolve().parent.parent),
                        help="Vault root directory")
    args = parser.parse_args()

    vault = Path(args.vault)
    registry_path = vault / "_db" / "entity-registry.json"
    output_path = vault / "_db" / "email-lookup.json"

    if not registry_path.exists():
        print("Error: entity-registry.json not found", file=sys.stderr)
        sys.exit(1)

    # Skip rebuild if lookup is already up-to-date (check registry + sanitize-mappings)
    sanitize_check_path = vault / "_db" / "sanitize-mappings.json"
    if output_path.exists():
        lookup_mtime = output_path.stat().st_mtime
        registry_mtime = registry_path.stat().st_mtime
        sanitize_mtime = sanitize_check_path.stat().st_mtime if sanitize_check_path.exists() else 0
        if lookup_mtime > registry_mtime and lookup_mtime > sanitize_mtime:
            print("email-lookup.json is up-to-date, skipping rebuild", file=sys.stderr)
            sys.exit(0)

    with open(registry_path, "r", encoding="utf-8") as f:
        registry = json.load(f)

    lookup: dict[str, dict] = {}
    people = registry.get("people", [])
    total_mapped = 0

    for person in people:
        link = person.get("link", "")
        emails = person.get("emails", [])
        vip_tier = person.get("vip")  # None if absent

        entry = {"wikilink": link}
        if vip_tier:
            entry["vip"] = vip_tier

        for email in emails:
            email_lower = email.strip().lower()
            if email_lower:
                lookup[email_lower] = entry
                total_mapped += 1

    # Merge sanitize-mapping tokens so they survive registry rebuilds
    sanitize_path = vault / "_db" / "sanitize-mappings.json"
    if sanitize_path.exists():
        try:
            with open(sanitize_path, "r", encoding="utf-8") as f:
                sanitize = json.load(f)
            token_count = 0
            for email_addr, token in sanitize.get("emails", {}).items():
                email_lower = email_addr.lower()
                if email_lower in lookup:
                    lookup[email_lower]["token"] = token
                    token_count += 1
            if token_count:
                print(f"Merged {token_count} PII tokens from sanitize-mappings",
                      file=sys.stderr)
        except Exception as e:
            print(f"Warning: Could not load sanitize-mappings.json: {e}",
                  file=sys.stderr)

    # Write lookup
    atomic_json_write(output_path, lookup)

    print(f"Mapped {total_mapped} email addresses from {len(people)} people",
          file=sys.stderr)


if __name__ == "__main__":
    main()
