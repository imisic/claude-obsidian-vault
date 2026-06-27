#!/usr/bin/env python3
"""
create-stubs.py: Create person stub files and update entity registry from
unresolved entities in the classify-inbox manifest.

Deterministic replacement for LLM-based stub creation in w-daily Step 3.1.

Usage:
    python3 create-stubs.py [--vault PATH] [--manifest PATH] [--dry-run]

Reads: _db/manifest.json (unresolved_entities from emails + transcripts)
Updates: 04-People/*.md, _db/entity-registry.json, _db/email-lookup.json,
         _db/sanitize-mappings.json
Output: JSON to stdout with results summary
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import company_from_domain, generate_pii_token, ensure_utf8_stdio


# Pipeline-internal labels that must never become people stubs.
# These leak in from speaker diarization placeholders, Plaud's attendee
# fallback, or Meeting Recorder's unknown-speaker tokens.
PIPELINE_LABEL_PATTERNS = [
    re.compile(r"^plaud[-_ ]?import$", re.IGNORECASE),
    re.compile(r"^voice[-_ ]?\d+$", re.IGNORECASE),
    re.compile(r"^speaker[-_ ]?\d+$", re.IGNORECASE),
    re.compile(r"^unknown$", re.IGNORECASE),
    re.compile(r"^sam$", re.IGNORECASE),  # self-reference, never a stub
]


def is_pipeline_label(value: str) -> bool:
    """Check if a name or email is a pipeline-internal label, not a real person."""
    if not value:
        return True
    v = value.strip()
    # Strip [[ ]] wrapping if present
    if v.startswith("[[") and v.endswith("]]"):
        v = v[2:-2]
    for pat in PIPELINE_LABEL_PATTERNS:
        if pat.match(v):
            return True
    return False


STUB_TEMPLATE = """---
name: "{name}"
company: "{company}"
role: ""
email: "{email}"
type: person
status: stub
---

# {name}

## Context
- **Role:**
- **Company/Market:**
- **How we work together:**

## Open action items
```dataview
TASK
WHERE !completed
WHERE contains(text, this.file.name)
WHERE !contains(file.path, "01-Daily") AND !contains(file.path, "_templates") AND !contains(file.path, "09-Archive")
```

## Interactions
```dataview
LIST
FROM "05-Interactions"
WHERE contains(file.outlinks, this.file.link)
SORT date DESC
LIMIT 10
```

## Notes
"""


def wikilink_to_filename(wikilink: str) -> str:
    """Convert [[FirstName-LastName]] to FirstName-LastName."""
    return wikilink.strip("[]")


def load_json(path: Path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json(path: Path, data):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(path))


def main():
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Create person stubs from manifest unresolved entities")
    parser.add_argument("--vault", default=str(Path(__file__).resolve().parent.parent),
                        help="Vault root directory")
    parser.add_argument("--manifest", default=None,
                        help="Path to manifest.json (default: _db/manifest.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be created without writing files")
    args = parser.parse_args()

    vault = Path(args.vault)
    manifest_path = Path(args.manifest) if args.manifest else vault / "_db" / "manifest.json"

    if not manifest_path.exists():
        print(json.dumps({"error": "manifest.json not found", "created_stubs": [], "registry_only": []}))
        sys.exit(1)

    manifest = load_json(manifest_path, {})
    people_dir = vault / "04-People"
    registry_path = vault / "_db" / "entity-registry.json"
    lookup_path = vault / "_db" / "email-lookup.json"
    sanitize_path = vault / "_db" / "sanitize-mappings.json"

    # Collect all unresolved entities from emails + transcripts + definitive_lows
    unresolved = {}  # email -> {wikilink, company, sources: int}
    for source_list in [
        manifest.get("email_manifest", []),
        manifest.get("transcripts", []),
        manifest.get("definitive_lows", []),
    ]:
        for item in source_list:
            for entity in item.get("unresolved_entities", []):
                email = entity.get("email", "").lower().strip()
                wikilink = entity.get("wikilink", "")
                if not email:
                    continue
                # Skip pipeline-internal labels (plaud-import, voice-NNN, etc.)
                if is_pipeline_label(email) or is_pipeline_label(wikilink):
                    print(f"Skipped pipeline label: {email or wikilink}", file=sys.stderr)
                    continue
                if email in unresolved:
                    unresolved[email]["sources"] += 1
                else:
                    unresolved[email] = {
                        "wikilink": wikilink,
                        "company": entity.get("company", "") or company_from_domain(email),
                        "sources": 1,
                    }

    if not unresolved:
        result = {"created_stubs": [], "registry_only": [], "errors": [], "message": "No unresolved entities"}
        print(json.dumps(result, indent=2))
        sys.exit(0)

    # Load existing data
    registry = load_json(registry_path, {"people": [], "products": [], "projects": [], "markets": [], "segments": [], "teams": []})
    lookup = load_json(lookup_path, {})
    sanitize = load_json(sanitize_path, {"emails": {}, "phones": {}, "token_to_pii": {}})

    # Build set of already-known emails + map archived people by email
    known_emails = set()
    archived_email_to_idx = {}  # email -> registry index, only for status=archived people
    for i, person in enumerate(registry.get("people", [])):
        is_archived = person.get("status") == "archived"
        for e in person.get("emails", []):
            el = e.lower().strip()
            known_emails.add(el)
            if is_archived:
                archived_email_to_idx[el] = i
    for e in lookup:
        known_emails.add(e.lower().strip())

    # Build set of existing people files (active dir only, _archived/ tracked separately)
    existing_files = set()
    if people_dir.exists():
        existing_files = {f.stem for f in people_dir.glob("*.md")}
    # Case-insensitive index (stem.lower() -> actual stem). On a case-insensitive
    # filesystem (Windows/WSL, macOS default) "Sam-rivera.md" and "Sam-Rivera.md"
    # are the same file, so a case-only mismatch must never trigger a stub write.
    existing_files_lower = {s.lower(): s for s in existing_files}
    archive_dir = people_dir / "_archived"
    archived_files = set()
    if archive_dir.exists():
        archived_files = {f.stem for f in archive_dir.glob("*.md")}

    # Count total recipients per email to determine stub threshold
    # We use the manifest email_manifest to count recipient lists
    email_recipient_counts = {}
    for email_item in manifest.get("email_manifest", []):
        to_list = email_item.get("to", [])
        cc_list = email_item.get("cc", [])
        total_recipients = len(to_list) + len(cc_list)
        for addr in to_list + cc_list:
            addr_lower = addr.strip().lower()
            if addr_lower:
                email_recipient_counts[addr_lower] = min(
                    email_recipient_counts.get(addr_lower, total_recipients),
                    total_recipients,
                )
    # Also count from definitive_lows
    for low_item in manifest.get("definitive_lows", []):
        to_list = low_item.get("to", [])
        cc_list = low_item.get("cc", [])
        total_recipients = len(to_list) + len(cc_list)
        for addr in to_list + cc_list:
            addr_lower = addr.strip().lower()
            if addr_lower:
                email_recipient_counts[addr_lower] = min(
                    email_recipient_counts.get(addr_lower, total_recipients),
                    total_recipients,
                )
    # And from transcripts, treat the attendees list as the "recipient" set.
    # Without this, transcript-only attendees default to 1 recipient and always
    # pass the ≤5 stub threshold, which is how a transcript with a large
    # attendee list could otherwise spawn one phantom stub per attendee.
    for transcript in manifest.get("transcripts", []):
        attendees = transcript.get("resolved_attendees", [])
        total_attendees = len(attendees)
        if total_attendees == 0:
            continue
        for att in attendees:
            addr_lower = (att.get("email") or "").strip().lower()
            if addr_lower:
                email_recipient_counts[addr_lower] = min(
                    email_recipient_counts.get(addr_lower, total_attendees),
                    total_attendees,
                )

    created_stubs = []
    registry_only = []
    resurrected = []
    skipped_case_collision = []
    errors = []
    existing_tokens = set(sanitize.get("token_to_pii", {}).keys())

    for email_addr, info in sorted(unresolved.items()):
        # Skip if already known, but first check if archived and needs resurrection
        if email_addr in known_emails:
            idx = archived_email_to_idx.get(email_addr)
            if idx is not None:
                person = registry["people"][idx]
                link = person.get("link", "")
                slug = link[2:-2] if link.startswith("[[") and link.endswith("]]") else None
                if slug and slug in archived_files and slug not in existing_files:
                    src = archive_dir / f"{slug}.md"
                    dst = people_dir / f"{slug}.md"
                    if args.dry_run:
                        print(f"[DRY RUN] Would resurrect: {slug}", file=sys.stderr)
                    else:
                        try:
                            os.rename(str(src), str(dst))
                            person.pop("status", None)
                            existing_files.add(slug)
                            existing_files_lower[slug.lower()] = slug
                            archived_files.discard(slug)
                            resurrected.append({
                                "email": email_addr,
                                "wikilink": link,
                                "slug": slug,
                            })
                            print(f"Resurrected from archive: {slug}", file=sys.stderr)
                        except Exception as e:
                            errors.append({"email": email_addr, "error": f"resurrect failed: {e}"})
                elif slug and slug in existing_files:
                    # File already in active dir somehow, just clear the flag
                    person.pop("status", None)
                    print(f"Cleared archived flag (file already active): {slug}", file=sys.stderr)
                else:
                    # Registry says archived but no file in either location: clear flag, log
                    person.pop("status", None)
                    print(f"Cleared archived flag (no file found): {slug}", file=sys.stderr)
            continue

        wikilink = info["wikilink"]
        company = info["company"]
        filename = wikilink_to_filename(wikilink)
        name = filename.replace("-", " ")

        # Case-collision guard: if a person file already exists whose stem differs
        # only by case, this "unresolved" entity is that real person under a
        # mis-cased name, not a new stub. It happens when a transcript attendee
        # header carries a hyphenated display name (e.g. "Sam-Rivera") that entity
        # resolution downcased into a pseudo-email local-part ("sam-rivera") and
        # failed to match. Writing the stub here would silently overwrite the real
        # file on a case-insensitive FS (the Sam-Rivera.md clobber). Skip it: no
        # registry entry, no file, and surface it for the run to resolve.
        if filename.lower() in existing_files_lower and filename not in existing_files:
            skipped_case_collision.append({
                "email": email_addr,
                "wikilink": wikilink,
                "existing_file": f"04-People/{existing_files_lower[filename.lower()]}.md",
            })
            print(f"Skipped case-collision stub: {filename}.md would clobber "
                  f"{existing_files_lower[filename.lower()]}.md", file=sys.stderr)
            continue

        # Determine stub threshold: create file if min recipient count <= 5
        min_recipients = email_recipient_counts.get(email_addr, 1)
        should_create_file = min_recipients <= 5

        # Add to entity registry
        registry_entry = {
            "link": wikilink,
            "name": name,
            "aliases": [],
            "emails": [email_addr],
            "company": company,
        }
        if not should_create_file:
            registry_entry["stub"] = False

        registry["people"].append(registry_entry)

        # Add to email lookup
        lookup[email_addr] = {"wikilink": wikilink}

        # Add to sanitize mappings
        if email_addr not in sanitize["emails"]:
            token = generate_pii_token("EMAIL", existing_tokens)
            sanitize["emails"][email_addr] = token
            sanitize["token_to_pii"][token] = email_addr
            existing_tokens.add(token)

        if should_create_file and filename not in existing_files:
            # Create stub file
            stub_content = STUB_TEMPLATE.format(
                name=name,
                company=company,
                email=email_addr,
            ).lstrip("\n")

            stub_path = people_dir / f"{filename}.md"
            if args.dry_run:
                print(f"[DRY RUN] Would create: {stub_path}", file=sys.stderr)
            else:
                try:
                    stub_path.write_text(stub_content, encoding="utf-8")
                    print(f"Created stub: {filename}.md", file=sys.stderr)
                except Exception as e:
                    errors.append({"email": email_addr, "error": str(e)})
                    continue

            existing_files.add(filename)
            existing_files_lower[filename.lower()] = filename
            created_stubs.append({
                "email": email_addr,
                "wikilink": wikilink,
                "company": company,
                "file": f"04-People/{filename}.md",
            })
        elif not should_create_file:
            registry_only.append({
                "email": email_addr,
                "wikilink": wikilink,
                "company": company,
                "reason": f"mass-cc (min {min_recipients} recipients)",
            })
        # else: file already exists, just updated registry + lookup

    # Save all updated databases
    if not args.dry_run and (created_stubs or registry_only or resurrected):
        save_json(registry_path, registry)
        save_json(lookup_path, lookup)
        save_json(sanitize_path, sanitize)
        print(f"Updated registry ({len(created_stubs)} stubs, "
              f"{len(registry_only)} registry-only, "
              f"{len(resurrected)} resurrected)", file=sys.stderr)

        # Append resurrection rows to audit CSV
        if resurrected:
            import datetime
            today = datetime.date.today().isoformat()
            csv_path = vault / "_db" / "people-archive-analysis.csv"
            if csv_path.exists():
                with open(csv_path, "a", encoding="utf-8") as f:
                    for r in resurrected:
                        f.write(f'{r["slug"]},RESURRECT,"reappeared in inbox {today}",{today},,,active\n')

    result = {
        "created_stubs": created_stubs,
        "registry_only": registry_only,
        "resurrected": resurrected,
        "skipped_case_collision": skipped_case_collision,
        "errors": errors,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
