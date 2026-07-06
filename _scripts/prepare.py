#!/usr/bin/env python3
"""prepare.py: single entry point for everything before agent dispatch in /w-daily v2.

Consolidates what used to be Phase 0 + Phase 1 of the skill (several separate
script calls plus a staging loop plus classify plus stubs) into one process so
the orchestrating LLM runs ONE command and reads ONE compact run-plan. All the
heavy lifting stays in the existing proven scripts; this file only sequences
them with internal parallelism.

Usage:
    python _scripts/prepare.py [--vault PATH] [--date YYYY-MM-DD]

Output: one JSON run-plan on stdout:
    {
      "classify":        <compact summary from classify-inbox.py>,
      "stubs":           <created_stubs/registry_only/resurrected from create-stubs.py>,
      "completeness_warning": "text or null",
      "staged_leftovers": ["_db/staged-notes/x.md", ...],
      "warnings":        [...],
      "errors":          [...]
    }

Exit code: 0 unless classification itself failed (the run cannot proceed
without a manifest; everything else is a warning).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import date as date_cls
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import ensure_utf8_stdio  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent


def run_script(cmd: list[str], vault: Path, timeout: int = 300) -> tuple[int, str, str]:
    """Run one helper script, forwarding its stderr to ours (progress lines
    stay visible in the terminal). Returns (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(
            cmd, cwd=str(vault), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        if proc.stderr:
            print(proc.stderr.rstrip(), file=sys.stderr)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s: {' '.join(cmd)}"
    except Exception as e:
        return 1, "", f"{e}"


def phase0(vault: Path, target_date: str, warnings: list[str]) -> str | None:
    """Index refresh, recording pull, calendar archive, transcript enrichment.

    Batch 1 runs in parallel (independent); batch 2 is sequential because
    enrichment depends on the pull + archive. Failures here never block the
    run: everything is either incremental or retried next morning.
    Returns the completeness warning text, if any.
    """
    py = sys.executable
    batch1 = [
        [py, str(SCRIPT_DIR / "backup-db.py"), "--vault", str(vault)],
        [py, str(SCRIPT_DIR / "build-thread-index.py"), "--vault", str(vault), "--incremental"],
        [py, str(SCRIPT_DIR / "build-email-lookup.py"), "--vault", str(vault)],
        ["bash", str(SCRIPT_DIR / "check-ingest-log.sh"), "--if-stale", str(vault)],
        [py, str(SCRIPT_DIR / "pull-plaud.py"), "--quiet"],
        [py, str(SCRIPT_DIR / "archive-calendar.py"), "--vault", str(vault), "--quiet"],
    ]
    procs = []
    for cmd in batch1:
        try:
            procs.append((cmd, subprocess.Popen(
                cmd, cwd=str(vault), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace")))
        except Exception as e:
            warnings.append(f"phase0 spawn failed for {Path(cmd[1]).name}: {e}")
    for cmd, p in procs:
        try:
            out, err = p.communicate(timeout=300)
        except subprocess.TimeoutExpired:
            p.kill()
            warnings.append(f"phase0 timeout: {Path(cmd[1]).name}")
            continue
        for stream in (err, out):
            if stream and stream.strip():
                print(stream.rstrip(), file=sys.stderr)
        if p.returncode != 0:
            warnings.append(f"phase0 nonzero exit ({p.returncode}): {Path(cmd[1]).name}")

    # Batch 2: enrichment chain, sequential by dependency.
    rc, _, _ = run_script([py, str(SCRIPT_DIR / "enrich-plaud-transcripts.py"),
                           "--vault", str(vault), "--quiet"], vault)
    if rc != 0:
        warnings.append("enrich-plaud-transcripts failed (non-fatal)")

    completeness_warning = None
    rc, out, err = run_script([py, str(SCRIPT_DIR / "check-plaud-completeness.py"),
                               "--vault", str(vault), "--date", target_date], vault)
    text = (out or "") + (err or "")
    # The checker prints an OK line or a warning block; only the warning is
    # worth carrying into the run-plan.
    if "OK" not in text and text.strip():
        completeness_warning = text.strip()

    return completeness_warning


def stage_inbox(vault: Path, warnings: list[str]) -> int:
    """Move processable inbox files into the staging dir. Calendar snapshots
    stay put (rewritten by every email pull, consumed by the capture app)."""
    inbox = vault / "00-Inbox"
    staging = inbox / "_processing"
    staging.mkdir(parents=True, exist_ok=True)
    moved = 0
    for f in sorted(inbox.iterdir()):
        if not f.is_file():
            continue
        if f.name.endswith("-calendar.json"):
            continue
        try:
            shutil.move(str(f), str(staging / f.name))
            moved += 1
        except Exception as e:
            warnings.append(f"staging move failed for {f.name}: {e}")
    return moved


def classify(vault: Path, errors: list[str]) -> dict | None:
    """Run classify-inbox.py and parse its compact summary. Fatal on failure:
    without a manifest there is nothing to dispatch."""
    cmd = [sys.executable, str(SCRIPT_DIR / "classify-inbox.py"),
           "--vault", str(vault),
           "--staging-dir", "00-Inbox/_processing",
           "--clean-bodies", "--sanitize-pii",
           "--thread-index", "_db/thread-index.json",
           "--resolve-entities"]
    rc, out, err = run_script(cmd, vault, timeout=600)
    if rc != 0:
        errors.append(f"classify-inbox failed (exit {rc}): {err.strip()[:500]}")
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        errors.append(f"classify-inbox output was not valid JSON: {e}")
        return None


def create_stubs(vault: Path, warnings: list[str]) -> dict:
    """Run create-stubs.py; stub creation failures are retried next run, so
    they only warn."""
    rc, out, _ = run_script([sys.executable, str(SCRIPT_DIR / "create-stubs.py"),
                             "--vault", str(vault)], vault)
    if rc != 0:
        warnings.append("create-stubs failed (stubs retried next run)")
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        warnings.append("create-stubs output was not valid JSON")
        return {}


def main():
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="w-daily v2 pre-dispatch entry point")
    parser.add_argument("--vault", default=".")
    parser.add_argument("--date", default=None, help="TARGET_DATE (default: today)")
    args = parser.parse_args()

    vault = Path(args.vault)
    target_date = args.date or date_cls.today().isoformat()
    warnings: list[str] = []
    errors: list[str] = []
    t0 = time.time()

    completeness_warning = phase0(vault, target_date, warnings)
    t1 = time.time()

    moved = stage_inbox(vault, warnings)
    print(f"Staged {moved} inbox files", file=sys.stderr)

    summary = classify(vault, errors)
    t2 = time.time()

    stubs = create_stubs(vault, warnings) if summary else {}

    # Leftover staged notes mean a previous run died between dispatch and
    # finalize. Their sources are still in _processing (classify just re-listed
    # them), so the fresh dispatch will regenerate them under collision-safe
    # names; the leftovers themselves need a decision (finalize or discard).
    staged_dir = vault / "_db" / "staged-notes"
    staged_dir.mkdir(parents=True, exist_ok=True)
    staged_leftovers = [str(p.relative_to(vault)) for p in sorted(staged_dir.glob("*.md"))]

    print(f"prepare: phase0 {t1 - t0:.1f}s, classify {t2 - t1:.1f}s, "
          f"total {time.time() - t0:.1f}s", file=sys.stderr)

    run_plan = {
        "classify": summary,
        "stubs": stubs,
        "completeness_warning": completeness_warning,
        "staged_leftovers": staged_leftovers,
        "warnings": warnings,
        "errors": errors,
    }
    json.dump(run_plan, sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
