---
name: w-prep
description: Prep brief for a meeting/conversation (forward) or a "what I did on a topic" recap for team-weekly reporting (retro), on a person and/or topic, across the vault plus any linked source-repo (e.g. project-repo).
model: claude-opus-4-6
user-invocable: true
argument-hint: "<person and/or topic> [last week | last month]"
allowed-tools: Read, Glob, Grep, Bash
---

# Prep / Recap

Build a brief on **$ARGUMENTS** from the vault and any linked source repo. Pick the lens from the phrasing:
- **Retro**: a time window ("last week/month", "this week") or "what I did / recap / for the weekly" → what moved on the topic in the window.
- **Forward** (default): prep for the conversation/meeting.

## Step 1: Parse the ask
- Resolve person(s) → `[[wikilink]]` (`.claude/rules/entity-matching.md`).
- Pull out topic keywords.
- Lens + window: retro if a window or "what I did / recap / weekly" is present (default 7 days; "month" = 30). Otherwise forward.

## Step 2: Refresh indexes
```bash
python _scripts/build-person-index.py
python _scripts/build-open-actions.py
```

## Step 3: Gather (vault)
- **Person named:** `python _scripts/prep-1on1-data.py --person "<Name>"` (history + carry-forward) + their open items from `_db/open-actions.json` (`by_person`).
- **Topic:** `rg -li "<keywords>" 05-Interactions/ 03-Projects/`, then read the matches. Retro: only those dated inside the window; forward: the most recent few. Read the related `03-Projects/<...>.md`.

## Step 4: Gather (linked repo, if any)
If a matched project note has a `source-repo:` field and the path exists on disk, follow **`w-project-status` Step 2.5**: Level 1 exec-site stages for the summary → Level 2 per-topic deep-dive on demand → `git -C "<source-repo>" log --since="<window>" --oneline`. The retro lens leans on the git log, that's the repo work the vault can't see. Skip silently if the repo isn't on this machine.

## Step 5: Synthesize
- **Forward:** Where it stands (1-2 lines) · Open threads likely to come up (grouped by topic, linked) · Open asks (yours / theirs) · From the repo (substance) · Talking points.
- **Retro (team-weekly ready):** terse accomplishment bullets: your meetings, decisions, completed actions, and repo commits/doc changes in the window. Phrase as "what I drove", not a transcript.

## Step 6: Output
- Print inline by default. If the ask says "save"/"note" or names a specific meeting, also write a `00-Inbox/` prep note with `meeting-prep: true` (two zones, prep discarded on processing, meeting zone carried forward; `/w-daily` handles it).
- Scan-layer: link to source notes, don't restate them. Keep it tight.
