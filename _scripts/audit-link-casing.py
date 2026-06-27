#!/usr/bin/env python3
"""
audit-link-casing.py: Report (and optionally fix) people-link casing drift.

A wikilink like [[Mia-Fischer]] that points at a file actually named
04-People/Mia-Fischer.md resolves fine in Obsidian on Windows/macOS
(case-insensitive FS) but breaks case-sensitive WSL scripts, indexes, and
entity matching. This audit treats the *filesystem* as canonical truth and
reports every link whose casing differs from its target file.

Usage:
    python3 audit-link-casing.py            # report only
    python3 audit-link-casing.py --fix      # rewrite drifted links to match files
"""

import argparse
import os
import re
import glob
from collections import Counter, defaultdict
from pathlib import Path

VAULT = Path(__file__).resolve().parent.parent
SKIP_PREFIXES = ("_attachments", "_templates", "_bases", ".obsidian",
                 ".claude", "_db", "_scripts", "node_modules")
LINK_RE = re.compile(r"\[\[([^\]\|#]+)")


def build_canon(people_dir: Path) -> dict[str, str]:
    """lowercased-slug -> canonical filename slug, for active + archived people."""
    canon: dict[str, str] = {}
    for d in (people_dir, people_dir / "_archived"):
        if not d.is_dir():
            continue
        for f in os.listdir(d):
            if f.endswith(".md"):
                slug = f[:-3]
                canon.setdefault(slug.lower(), slug)
    return canon


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit people-link casing drift")
    ap.add_argument("--vault", default=str(VAULT))
    ap.add_argument("--fix", action="store_true", help="rewrite drifted links to canonical")
    args = ap.parse_args()

    vault = Path(args.vault)
    os.chdir(vault)
    canon = build_canon(vault / "04-People")

    drift = Counter()                      # (used, canonical) -> count
    files_with = defaultdict(set)          # (used, canonical) -> {paths}

    for path in glob.glob("**/*.md", recursive=True):
        if path.startswith(SKIP_PREFIXES):
            continue
        try:
            txt = open(path, encoding="utf-8").read()
        except Exception:
            continue
        for m in LINK_RE.finditer(txt):
            t = m.group(1).strip()
            low = t.lower()
            if low in canon and t != canon[low]:
                drift[(t, canon[low])] += 1
                files_with[(t, canon[low])].add(path)

    if not drift:
        print("No people-link casing drift. Links match filesystem.")
        return 0

    total = sum(drift.values())
    print(f"Casing drift: {total} link instances, {len(drift)} distinct pairs\n")
    for (used, c), n in drift.most_common():
        print(f"{n:5d}  [[{used}]]  ->  [[{c}]]   ({len(files_with[(used, c)])} files)")

    if not args.fix:
        print("\nRe-run with --fix to normalize links to the filesystem casing.")
        return 1

    pairs = {used: c for (used, c) in drift}
    pat = {used: re.compile(r"\[\[" + re.escape(used) + r"(?=[\]\|#])") for used in pairs}
    changed_files = 0
    changed_links = 0
    for path in glob.glob("**/*.md", recursive=True):
        if path.startswith(SKIP_PREFIXES):
            continue
        try:
            txt = open(path, encoding="utf-8").read()
        except Exception:
            continue
        orig, n_file = txt, 0
        for used, c in pairs.items():
            txt, n = pat[used].subn("[[" + c, txt)
            n_file += n
        if n_file:
            open(path, "w", encoding="utf-8").write(txt)
            changed_files += 1
            changed_links += n_file
    print(f"\nFixed {changed_links} links across {changed_files} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
