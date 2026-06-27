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
LIST_CAP = 30  # max items shown per section in the markdown report


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


def render_md(today, overdue, stubs, archive, ghosts, quarters):
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

    if args.json:
        print(json.dumps({
            "date": today.isoformat(),
            "overdue_actions": overdue,
            "stale_stubs": stubs,
            "archive_candidates": archive,
            "ghost_log_entries": ghosts,
        }, indent=2))
        return

    report = render_md(today, overdue, stubs, archive, ghosts, args.archive_quarters)
    out_path = vault / "_db" / "maintenance-todo.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    def n(x):
        return "n/a" if x is None else len(x)

    print(f"Vault health ({today.isoformat()}): "
          f"{n(overdue)} overdue, {len(stubs)} stale stubs, "
          f"{len(archive)} archive candidates, {n(ghosts)} ghost log entries.",
          file=sys.stderr)
    print(f"Report written to {out_path.relative_to(vault)}", file=sys.stderr)


if __name__ == "__main__":
    main()
