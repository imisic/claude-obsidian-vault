#!/usr/bin/env python3
"""
vault-health.py: detect-only maintenance report for the vault.

Surfaces rot that nothing else flags proactively, writes a task list to
_db/maintenance-todo.md, and prints a summary. It NEVER fixes anything: the
report tells you (or an agent) what to do, in the detect-then-delegate shape.
Run it on a schedule (cron), or ad hoc.

Checks:
  - Overdue open actions (from _db/open-actions.json)
  - Stale people stubs (04-People/*.md still `status: stub`, older than N days)
  - Archive-candidate interactions (older than 2 quarters, no open action)
  - Ghost ingest-log entries (action=created but output-file is gone)
  - Broken wikilinks (a [[target]] that is neither a note nor a known entity)
  - Orphaned entity notes (04-People / 03-Projects notes with no inbound links)
  - Unresolved entities (notes create-stubs.py could not place a name for)

Usage:
    python3 vault-health.py [--vault PATH] [--stale-stub-days 14]
                            [--archive-quarters 2] [--json]
"""

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

FM_DATE_RE = re.compile(r"^date:\s*(\d{4}-\d{2}-\d{2})", re.M)
FM_STATUS_RE = re.compile(r"^status:\s*(\S+)", re.M)
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
UNRESOLVED_HDR_RE = re.compile(r"^unresolved-entities:(.*)$", re.M)
EXT_RE = re.compile(r"\.[a-z0-9]{1,5}$", re.I)  # attachment/base embeds, not notes
LIST_CAP = 30  # max items shown per section in the markdown report

# Content roots scanned for the coherence checks. Infrastructure dirs are
# excluded on purpose: _db holds this report (which is full of [[links]]),
# _templates holds placeholder links, and the rest are not "wiki" notes.
CONTENT_DIRS = ["00-Inbox", "01-Daily", "03-Projects", "04-People",
                "05-Interactions", "07-Areas", "08-Reference", "09-Archive"]
ENTITY_DIRS = ("04-People", "03-Projects")  # where an orphan is worth flagging


def _link_slug(raw):
    """Normalize a wikilink's inner text to its target note slug.

    Drops a `|display` alias and a `#heading`/`#^block` anchor, then takes the
    basename since Obsidian resolves `[[folder/Note]]` by filename, not path.
    Table cells escape the alias pipe (backslash-pipe), so unescape it first.
    """
    t = raw.replace("\\|", "|").split("|", 1)[0].split("#", 1)[0].strip()
    return t.split("/")[-1] if t else ""


def _frontmatter(content):
    """Return the frontmatter block text, or '' if none."""
    if not content.startswith("---"):
        return ""
    end = content.find("---", 3)
    return content[3:end] if end > 0 else ""


def _parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _unresolved_slugs(fm):
    """Slugs from a note's `unresolved-entities:` frontmatter (block or inline)."""
    m = UNRESOLVED_HDR_RE.search(fm)
    if not m:
        return []
    out = []
    inline = m.group(1).strip()
    if inline and inline != "[]":  # inline form: unresolved-entities: [x, y]
        for tok in WIKILINK_RE.finditer(inline):
            out.append(_link_slug(tok.group(1)))
        return [s for s in out if s]
    for line in fm[m.end():].splitlines():  # block form: one `- "[[X]]"` per line
        s = line.strip()
        if s.startswith("- "):
            for tok in WIKILINK_RE.finditer(s):
                out.append(_link_slug(tok.group(1)))
        elif s:
            break  # next frontmatter key ends the list
    return [s for s in out if s]


def _walk_content_notes(vault):
    """Read every content note once; return the data the coherence checks need."""
    notes = []
    for d in CONTENT_DIRS:
        root = vault / d
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.md")):
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm = _frontmatter(content)
            links = {_link_slug(m.group(1)) for m in WIKILINK_RE.finditer(content)}
            links = {s for s in links  # drop embeds and punctuation-only ([[...]])
                     if s and not EXT_RE.search(s) and any(c.isalnum() for c in s)}
            links.discard(p.stem)  # a note linking itself is not a cross-reference
            dm = FM_DATE_RE.search(fm)
            notes.append({
                "stem": p.stem,
                "path": str(p.relative_to(vault)),
                "dir": d,
                "links": links,
                "unresolved": _unresolved_slugs(fm),
                "date": dm.group(1) if dm else None,
            })
    return notes


def _registry_slugs(vault):
    """Lowercased slugs for every known registry entity, file-backed or not.

    Mass-CC people are recorded in the registry with `stub: false` and no note
    file; their `[[links]]` are intentional, not rot, so they must count as
    known targets or broken-link detection drowns in false positives.
    """
    data = _load_json(vault / "_db" / "entity-registry.json")
    slugs = set()
    if not data:
        return slugs
    people = data.get("people", {})
    entries = people.values() if isinstance(people, dict) else people
    for e in entries:
        m = re.search(r"\[\[([^\]|#]+)", e.get("link", ""))
        if m:
            slugs.add(m.group(1).strip().split("/")[-1].lower())
        name = (e.get("name") or "").strip()
        if name:
            slugs.add(name.replace(" ", "-").lower())
        for a in e.get("aliases") or []:
            slugs.add(a.strip().replace(", ", "-").replace(" ", "-").lower())
    return slugs


def broken_wikilinks(notes, known_slugs):
    """Wikilinks pointing at a slug that is neither a note nor a known entity.

    Matching is case-insensitive so a casing-only drift is left to
    audit-link-casing.py rather than double-reported here as a dead link.
    """
    out = []
    for n in notes:
        skip = {u.lower() for u in n["unresolved"]}  # already-known-pending
        for t in sorted(n["links"]):
            if t.lower() not in known_slugs and t.lower() not in skip:
                out.append({"source": n["stem"], "path": n["path"], "target": t})
    return out


def orphaned_entities(notes, today, min_age_days):
    """People/Project notes that nothing else links to (skipping fresh stubs)."""
    inbound = {}
    for n in notes:
        for t in n["links"]:
            inbound[t.lower()] = inbound.get(t.lower(), 0) + 1
    out = []
    for n in notes:
        if n["dir"] not in ENTITY_DIRS or inbound.get(n["stem"].lower(), 0):
            continue
        d = _parse_date(n["date"] or "")
        if d and (today - d).days < min_age_days:
            continue  # a just-created note has not had time to be linked yet
        out.append({"note": n["stem"], "dir": n["dir"], "path": n["path"]})
    return out


def unresolved_entity_notes(notes):
    """Notes still carrying names create-stubs.py could not place."""
    return [{"note": n["stem"], "path": n["path"], "entities": n["unresolved"]}
            for n in notes if n["unresolved"]]


def overdue_actions(vault, today):
    """List of overdue open actions, or None if the index is missing."""
    data = _load_json(vault / "_db" / "open-actions.json")
    if not data:
        return None
    out = []
    for owner, actions in (data.get("by_owner") or {}).items():
        for a in actions:
            due = _parse_date(a.get("due", ""))
            if due and due < today:
                out.append({
                    "owner": owner,
                    "description": a.get("description", ""),
                    "due": a.get("due"),
                    "days": (today - due).days,
                    "source": a.get("source", ""),
                })
    out.sort(key=lambda x: x["days"], reverse=True)
    return out


def stale_stubs(vault, today, stale_days):
    out = []
    people = vault / "04-People"
    if not people.is_dir():
        return out
    for p in sorted(people.glob("*.md")):
        try:
            fm = _frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        sm = FM_STATUS_RE.search(fm)
        if not sm or sm.group(1) != "stub":
            continue
        dm = FM_DATE_RE.search(fm)
        d = _parse_date(dm.group(1)) if dm else None
        if d is None:  # no date in frontmatter, fall back to file mtime
            d = date.fromtimestamp(p.stat().st_mtime)
        if (today - d).days >= stale_days:
            out.append({"name": p.stem, "since": d.isoformat(),
                        "path": str(p.relative_to(vault))})
    return out


def archive_candidates(vault, today, quarters):
    """Interactions older than `quarters` quarters with no open action."""
    cutoff = today - timedelta(days=quarters * 91)
    oa = _load_json(vault / "_db" / "open-actions.json") or {}
    busy = set()
    for actions in (oa.get("by_owner") or {}).values():
        for a in actions:
            if a.get("source_path"):
                busy.add(a["source_path"])
    out = []
    inter = vault / "05-Interactions"
    if not inter.is_dir():
        return out
    for p in sorted(inter.rglob("*.md")):
        rel = str(p.relative_to(vault))
        if rel in busy:
            continue
        try:
            fm = _frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        dm = FM_DATE_RE.search(fm)
        d = _parse_date(dm.group(1)) if dm else None
        if d and d < cutoff:
            out.append({"note": p.stem, "date": d.isoformat(), "path": rel})
    out.sort(key=lambda x: x["date"])
    return out


def ghost_log_entries(vault):
    """ingest-log entries that claim a created note whose file is gone. None if no log."""
    data = _load_json(vault / "_db" / "ingest-log.json")
    if data is None:
        return None
    entries = data if isinstance(data, list) else data.get("entries", [])
    out = []
    for e in entries:
        if e.get("action") == "created":
            of = e.get("output-file")
            if of and not (vault / of).exists():
                out.append({"source": e.get("source-file", ""), "missing": of})
    return out


def _section(lines, title, items, missing_hint, empty_ok, render):
    """Append one report section. items=None means the source was missing."""
    count = "n/a" if items is None else len(items)
    lines.append(f"## {title} ({count})")
    if items is None:
        lines.append(f"- {missing_hint}")
    elif not items:
        lines.append(f"- {empty_ok}")
    else:
        for it in items[:LIST_CAP]:
            lines.append(render(it))
        if len(items) > LIST_CAP:
            lines.append(f"- ... and {len(items) - LIST_CAP} more")
    lines.append("")


def render_md(today, overdue, stubs, archive, ghosts, broken, orphans,
              unresolved, quarters):
    lines = [
        f"# Vault maintenance ({today.isoformat()})",
        "",
        "Detect-only report from `vault-health.py`. Nothing here is auto-fixed; each",
        "section says how to resolve it. Regenerate by re-running the script.",
        "",
    ]
    _section(lines, "Overdue actions", overdue,
             "`_db/open-actions.json` missing: run `python _scripts/build-open-actions.py --vault .`",
             "None.",
             lambda a: f"- [[{a['owner']}]] {a['description']} (due {a['due']}, {a['days']}d overdue) -> [[{a['source']}]]")
    _section(lines, "Stale people stubs", stubs, "", "None.",
             lambda s: f"- [[{s['name']}]] (stub since {s['since']}) -> {s['path']}")
    _section(lines, f"Archive candidates (>{quarters} quarters, no open action)", archive, "", "None.",
             lambda c: f"- [[{c['note']}]] ({c['date']}) -> move to 09-Archive/")
    _section(lines, "Ghost ingest-log entries", ghosts,
             "no `_db/ingest-log.json` yet (nothing ingested).",
             "None.",
             lambda g: f"- {g['source']} -> missing {g['missing']} (run `_scripts/check-ingest-log.sh`)")
    _section(lines, "Broken wikilinks", broken, "", "None.",
             lambda b: f"- [[{b['source']}]] -> [[{b['target']}]] (no such note or known entity)")
    _section(lines, "Orphaned entity notes (no inbound links)", orphans, "", "None.",
             lambda o: f"- [[{o['note']}]] ({o['dir']}) -> nothing links here; enrich or archive")
    _section(lines, "Unresolved entities (pipeline could not place)", unresolved, "", "None.",
             lambda u: f"- [[{u['note']}]] -> {', '.join('[[' + e + ']]' for e in u['entities'])}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Detect-only vault maintenance report")
    parser.add_argument("--vault", default=str(Path(__file__).resolve().parent.parent),
                        help="Vault root directory")
    parser.add_argument("--stale-stub-days", type=int, default=14,
                        help="Flag person stubs older than this many days (default: 14)")
    parser.add_argument("--archive-quarters", type=int, default=2,
                        help="Flag interactions older than this many quarters (default: 2)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    vault = Path(args.vault)
    today = date.today()

    overdue = overdue_actions(vault, today)
    stubs = stale_stubs(vault, today, args.stale_stub_days)
    archive = archive_candidates(vault, today, args.archive_quarters)
    ghosts = ghost_log_entries(vault)

    notes = _walk_content_notes(vault)
    known = {n["stem"].lower() for n in notes} | _registry_slugs(vault)
    broken = broken_wikilinks(notes, known)
    orphans = orphaned_entities(notes, today, args.stale_stub_days)
    unresolved = unresolved_entity_notes(notes)

    if args.json:
        print(json.dumps({
            "date": today.isoformat(),
            "overdue_actions": overdue,
            "stale_stubs": stubs,
            "archive_candidates": archive,
            "ghost_log_entries": ghosts,
            "broken_wikilinks": broken,
            "orphaned_entities": orphans,
            "unresolved_entities": unresolved,
        }, indent=2))
        return

    report = render_md(today, overdue, stubs, archive, ghosts, broken, orphans,
                       unresolved, args.archive_quarters)
    out_path = vault / "_db" / "maintenance-todo.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    def n(x):
        return "n/a" if x is None else len(x)

    print(f"Vault health ({today.isoformat()}): "
          f"{n(overdue)} overdue, {len(stubs)} stale stubs, "
          f"{len(archive)} archive candidates, {n(ghosts)} ghost log entries, "
          f"{len(broken)} broken links, {len(orphans)} orphans, "
          f"{len(unresolved)} unresolved.",
          file=sys.stderr)
    print(f"Report written to {out_path.relative_to(vault)}", file=sys.stderr)


if __name__ == "__main__":
    main()
