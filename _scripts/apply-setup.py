#!/usr/bin/env python3
"""apply-setup.py: write the structured parts of a /w-setup run (deterministic).

Reads _db/setup-answers.json and writes the config that benefits from exact,
testable, idempotent writing:
- _scripts/utils.py          OWNER CONFIG block (between the markers)
- _db/entity-registry.json   people (owner + manager + VIPs) and projects
- .obsidian/bookmarks.json   point at the owner note
- _scripts/.env              from .env.example (only if Plaud chosen and .env absent)

The prose edits (CLAUDE.md "About me", the vip.md tier table, renaming the
example person/project notes) are done by the /w-setup skill after this runs:
those are content edits an LLM handles more cleanly than string surgery.

Idempotent: the utils block is marker-bounded and the JSON files are overwritten.

Usage: python3 apply-setup.py [--answers PATH] [--vault PATH]
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import ensure_utf8_stdio, atomic_json_write, atomic_text_write  # noqa: E402

OWNER_BLOCK_RE = re.compile(r"# >>> OWNER CONFIG.*?# <<< OWNER CONFIG <<<", re.DOTALL)


def slugify(name: str) -> str:
    # "Alex Kim" -> "Alex-Kim"; an already-hyphenated name is left as-is.
    return re.sub(r"\s+", "-", name.strip())


def _py_set(values) -> str:
    if not values:
        return "set()"
    return "{\n" + "".join(f'    "{v}",\n' for v in values) + "}"


def render_owner_block(o: dict) -> str:
    return (
        "# >>> OWNER CONFIG (single source of truth: edit here or via /w-setup) >>>\n"
        "# Your wikilink slug, name, company, email addresses, and timezone. Every\n"
        "# pipeline script imports these from utils; this is the ONLY place to change them.\n"
        f'OWNER_SLUG = "{o["slug"]}"\n'
        f'OWNER_NAME = "{o["name"]}"\n'
        f'OWNER_COMPANY = "{o.get("company", "")}"\n'
        "# Personal / non-work addresses (self-forwards land here):\n"
        f"OWNER_PERSONAL_EMAILS = {_py_set(o.get('personal_emails', []))}\n"
        "# Work addresses:\n"
        f"OWNER_WORK_EMAILS = {_py_set(o.get('work_emails', []))}\n"
        '# Union, for general "is this the owner?" checks:\n'
        "OWNER_EMAILS = OWNER_PERSONAL_EMAILS | OWNER_WORK_EMAILS\n"
        "# IANA timezone for converting calendar timestamps:\n"
        f'LOCAL_TZ = "{o.get("timezone", "America/New_York")}"\n'
        "# <<< OWNER CONFIG <<<"
    )


def build_registry(ans: dict) -> dict:
    o = ans["owner"]
    people = [{
        "name": o["name"],
        "link": f'[[{o["slug"]}]]',
        "aliases": [],
        "company": o.get("company", ""),
        "emails": list(o.get("personal_emails", [])) + list(o.get("work_emails", [])),
    }]
    mgr = ans.get("manager") or {}
    if mgr.get("name"):
        people.append({
            "name": mgr["name"],
            "link": f'[[{mgr.get("slug") or slugify(mgr["name"])}]]',
            "aliases": [],
            "company": mgr.get("company", o.get("company", "")),
            "emails": [mgr["email"]] if mgr.get("email") else [],
            "vip": "boss-chain",
        })
    for v in ans.get("vips", []):
        people.append({
            "name": v["name"],
            "link": f'[[{v.get("slug") or slugify(v["name"])}]]',
            "aliases": [],
            "company": v.get("company", o.get("company", "")),
            "emails": [v["email"]] if v.get("email") else [],
            "vip": v["tier"],
        })
    registry = {"people": people}
    projects = [{"name": p["name"], "link": f'[[{p.get("slug") or slugify(p["name"])}]]', "aliases": []}
                for p in ans.get("projects", [])]
    if projects:
        registry["projects"] = projects
    return registry


def main():
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description="Apply /w-setup answers to vault config")
    ap.add_argument("--vault", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--answers", default=None)
    args = ap.parse_args()

    vault = Path(args.vault)
    answers_path = Path(args.answers) if args.answers else vault / "_db" / "setup-answers.json"
    ans = json.loads(answers_path.read_text(encoding="utf-8"))
    owner = ans["owner"]
    written = []

    # 1. utils.py owner block (marker-bounded, idempotent)
    utils_path = vault / "_scripts" / "utils.py"
    text = utils_path.read_text(encoding="utf-8")
    if OWNER_BLOCK_RE.search(text):
        atomic_text_write(utils_path, OWNER_BLOCK_RE.sub(lambda _m: render_owner_block(owner), text))
        written.append("_scripts/utils.py")
    else:
        print("WARN: OWNER CONFIG markers not found in utils.py; skipped", file=sys.stderr)

    # 2. entity-registry.json
    atomic_json_write(vault / "_db" / "entity-registry.json", build_registry(ans))
    written.append("_db/entity-registry.json")

    # 3. bookmarks.json -> owner note
    bm = vault / ".obsidian" / "bookmarks.json"
    if bm.exists():
        atomic_json_write(bm, {"items": [{"type": "file", "path": f'04-People/{owner["slug"]}.md'}]})
        written.append(".obsidian/bookmarks.json")

    # 4. .env from example (only if Plaud chosen and .env absent)
    if (ans.get("integrations") or {}).get("plaud"):
        env, example = vault / "_scripts" / ".env", vault / "_scripts" / ".env.example"
        if example.exists() and not env.exists():
            atomic_text_write(env, example.read_text(encoding="utf-8"))
            written.append("_scripts/.env (copied from .env.example, add your token)")

    mgr = ans.get("manager") or {}
    mgr_slug = (mgr.get("slug") or slugify(mgr["name"])) if mgr.get("name") else None
    print(json.dumps({
        "written": written,
        "owner_slug": owner["slug"],
        "manager_slug": mgr_slug,
        "vip_count": len(ans.get("vips", [])),
        "pm_features": ans.get("pm_features", True),
    }, indent=2))


if __name__ == "__main__":
    main()
