#!/usr/bin/env python3
"""finish.py: Commit and push this run's vault content (w-daily Phase 6).

Ports the SKILL.md Phase 6 git logic: stale-lock age guard, an allowlist stage of
only the content trees /w-daily writes, a commit only when something is staged, and
a push only when there are unpushed commits. Push failure is categorized, never
fatal: data is safe on local disk and the next run retries.

Usage:
    python3 finish.py [--vault PATH] [--date YYYY-MM-DD]

Output (stdout): JSON with lock_status, committed, commit_sha, unpushed, pushed,
category, detail. ALWAYS exits 0.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date as date_cls
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import ensure_utf8_stdio  # noqa: E402

# Content trees /w-daily produces: notes, stubs, attachments, indexes, inbox
# deletions of processed emails. Excludes .claude, _scripts, _templates, root.
ALLOWLIST_TREES = [
    "00-Inbox", "01-Daily", "03-Projects", "04-People", "05-Interactions",
    "07-Areas", "08-Reference", "09-Archive", "_attachments", "_db",
]
LOCK_STALE_MINUTES = 5


def run_git(vault: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(vault), env=env,
                          capture_output=True, text=True)


def finish(vault: Path, commit_date: str) -> dict:
    env = {**os.environ, "SSH_AUTH_SOCK": str(Path.home() / ".ssh" / "agent.sock")}
    result = {"lock_status": "ok", "committed": False, "commit_sha": None,
              "unpushed": 0, "pushed": 0, "category": "in-sync", "detail": ""}

    # Stale index.lock guard: a crashed Obsidian or background git op leaves
    # .git/index.lock behind and blocks every write. Decide purely on AGE: a lock
    # older than 5 min is stale (no legitimate index op here holds it that long).
    lock = vault / ".git" / "index.lock"
    if lock.exists():
        age_min = (time.time() - lock.stat().st_mtime) / 60.0
        if age_min > LOCK_STALE_MINUTES:
            try:
                lock.unlink()
            except OSError:
                pass
            result["lock_status"] = "stale-cleared"
        else:
            # A git write is probably in progress: defer commit + push this run.
            result["lock_status"] = "busy-deferred"
            result["category"] = "busy-deferred"
            result["detail"] = ("a recent .git/index.lock (under 5 min) suggests a git write "
                                "in progress; commit + push deferred to the next run "
                                "(data safe on local disk)")
            return result

    # Allowlist stage: only content trees that exist (a missing tree would fail
    # the whole `git add` and strand the rest).
    trees = [t for t in ALLOWLIST_TREES if (vault / t).exists()]
    if trees:
        run_git(vault, env, "add", *trees)
    # Drop the calendar snapshot: rewritten every email pull, not a run output.
    calendars = [str(p.relative_to(vault)) for p in (vault / "00-Inbox").glob("*-calendar.json")]
    if calendars:
        run_git(vault, env, "reset", "-q", "--", *calendars)

    # Commit only if the run actually staged something.
    if run_git(vault, env, "diff", "--cached", "--quiet").returncode != 0:
        commit = run_git(vault, env, "commit", "-m", f"w-daily: {commit_date}")
        if commit.returncode == 0:
            result["committed"] = True
            sha = run_git(vault, env, "rev-parse", "HEAD")
            if sha.returncode == 0:
                result["commit_sha"] = sha.stdout.strip()
        else:
            result["category"] = "other"
            result["detail"] = (commit.stderr or commit.stdout).strip()

    # Push only if there are unpushed commits. A failed rev-list (e.g. no
    # origin/main) yields 0, matching the SKILL's `${unpushed:-0}` default.
    rev = run_git(vault, env, "rev-list", "--count", "origin/main..HEAD")
    unpushed = int(rev.stdout.strip()) if rev.returncode == 0 and rev.stdout.strip().isdigit() else 0
    result["unpushed"] = unpushed

    if unpushed > 0:
        push = run_git(vault, env, "push", "origin", "main")
        out = (push.stdout + push.stderr)
        if push.returncode == 0:
            result["pushed"] = unpushed
            result["category"] = "pushed"
            result["detail"] = f"pushed {unpushed} commit{'s' if unpushed != 1 else ''}"
        elif "permission denied (publickey)" in out.lower():
            result["category"] = "publickey"
            result["detail"] = ("push failed: SSH key not loaded. Run ssh-add for "
                                "your git key, then rerun /w-daily")
        elif any(s in out for s in ("Could not resolve", "Connection refused", "Broken pipe")):
            result["category"] = "network"
            result["detail"] = "push failed: network unreachable"
        else:
            result["category"] = "other"
            result["detail"] = out.strip()
    elif result["category"] != "other":
        # Nothing to push and no commit failure: in sync.
        result["category"] = "in-sync"
        result["detail"] = "in sync"

    return result


def main() -> int:
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description="Commit and push w-daily run content")
    ap.add_argument("--vault", default=".")
    ap.add_argument("--date", default=date_cls.today().isoformat(), help="Commit message date")
    args = ap.parse_args()

    # Push failure must never fail the run: catch everything, still emit JSON, exit 0.
    try:
        result = finish(Path(args.vault).resolve(), args.date)
    except Exception as e:
        result = {"lock_status": "ok", "committed": False, "commit_sha": None,
                  "unpushed": 0, "pushed": 0, "category": "other", "detail": str(e)}

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
