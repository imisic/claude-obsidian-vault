#!/usr/bin/env python3
"""check-environment.py: doctor for the vault. Reports optional tools AND vault integrity.

Used by /w-setup, and runnable anytime. Standard library only.

Two sections:
  - Optional tools: PDF, HTML, images, and text process with zero installs (Claude
    reads them natively); the tools below just add coverage. None are required.
  - Vault integrity: the _db files the pipeline depends on. A failure here means
    /w-daily will misbehave; each failure prints the fix.

Exit code: 0 normally (never fails the run by default). With --strict, exits 1 if
any REQUIRED check failed, so it can gate a setup step or CI.

Usage:
  python3 check-environment.py            # human-readable report
  python3 check-environment.py --json     # machine-readable (for the skill)
  python3 check-environment.py --strict   # exit 1 if a required check fails
  python3 check-environment.py --vault PATH
"""
import argparse
import json
import shutil
import sys
from pathlib import Path


def build_tool_checks():
    return [
        {
            "key": "python",
            "name": "Python 3.10+",
            "ok": sys.version_info >= (3, 10),
            "required": True,
            "unlocks": "the core pipeline (required)",
            "fix": "install Python 3.10 or newer",
        },
        {
            "key": "markitdown",
            "name": "markitdown",
            "ok": shutil.which("markitdown") is not None,
            "required": False,
            "unlocks": "DOCX / PPTX / XLSX conversion (PDF already works without it)",
            "fix": "pip install markitdown",
        },
        {
            "key": "defuddle",
            "name": "defuddle",
            "ok": shutil.which("defuddle") is not None,
            "required": False,
            "unlocks": "cleaner HTML extraction (HTML already works without it)",
            "fix": "npm install -g defuddle-cli  (see github.com/kepano/defuddle)",
        },
        {
            "key": "plaud",
            "name": "Plaud CLI",
            "ok": shutil.which("plaud") is not None,
            "required": False,
            "unlocks": "Plaud NotePin transcript sync (optional)",
            "fix": "npm install -g @plaud-ai/cli, then run `plaud login`",
        },
    ]


def _json_ok(path):
    """True if path exists and parses as JSON."""
    try:
        with open(path, encoding="utf-8") as fh:
            json.load(fh)
        return True
    except (OSError, ValueError):
        return False


def build_vault_checks(vault):
    db = vault / "_db"
    registry = db / "entity-registry.json"
    ingest_log = db / "ingest-log.json"
    return [
        {
            "key": "db-dir",
            "name": "_db/ directory",
            "ok": db.is_dir(),
            "required": True,
            "unlocks": "registry, logs, and indexes the pipeline reads",
            "fix": f"create it: mkdir -p {db}",
        },
        {
            "key": "entity-registry",
            "name": "_db/entity-registry.json parses",
            "ok": registry.is_file() and _json_ok(registry),
            "required": True,
            "unlocks": "entity resolution (people/projects/products to wikilinks)",
            "fix": "run /w-setup to (re)build it, or restore from _db/backups/",
        },
        {
            "key": "ingest-log",
            "name": "_db/ingest-log.json parses (if present)",
            "ok": (not ingest_log.exists()) or _json_ok(ingest_log),
            "required": True,
            "unlocks": "duplicate detection and the log-integrity check in /w-daily",
            "fix": "corrupt log: restore from _db/backups/, or delete it to start a fresh log",
        },
    ]


def _emit_human(title, checks):
    print(f"\n{title}:\n")
    for c in checks:
        flag = "OK" if c["ok"] else ("FAIL" if c["required"] else "--")
        print(f"  [{flag}] {c['name']}")
        print(f"       unlocks: {c['unlocks']}")
        if not c["ok"]:
            print(f"       fix: {c['fix']}")


def main():
    parser = argparse.ArgumentParser(description="Doctor: optional tools + vault integrity")
    parser.add_argument("--vault", default=str(Path(__file__).resolve().parent.parent),
                        help="Vault root directory")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 if any required check fails")
    args = parser.parse_args()

    vault = Path(args.vault)
    tools = build_tool_checks()
    vault_checks = build_vault_checks(vault)
    all_checks = tools + vault_checks

    if args.json:
        keys = ("key", "name", "ok", "required", "unlocks", "fix")
        print(json.dumps([{k: c[k] for k in keys} for c in all_checks]))
    else:
        _emit_human("Optional tools", tools)
        _emit_human("Vault integrity", vault_checks)
        print("\nNothing in 'Optional tools' is required to start: PDF, HTML, images, "
              "and text process with zero installs.\n")

    required_failed = [c for c in all_checks if c["required"] and not c["ok"]]
    if args.strict and required_failed:
        if not args.json:
            print(f"{len(required_failed)} required check(s) failed.\n", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
