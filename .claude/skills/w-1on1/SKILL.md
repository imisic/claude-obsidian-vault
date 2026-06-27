---
name: w-1on1
description: Prepare for a 1on1 meeting with a specific person. Gathers previous meeting history, open action items, and creates a pre-populated meeting note.
user-invocable: true
argument-hint: "[Person Name]"
---

# 1on1 Prep

Prepare for a 1on1 meeting with $ARGUMENTS.

## Phase 0: Build indexes (< 2 seconds)

Run these two scripts in parallel:
```bash
python _scripts/build-person-index.py
python _scripts/build-open-actions.py
```

Then extract targeted data for this person:
```bash
python _scripts/prep-1on1-data.py --person "$ARGUMENTS"
```

The script uses **differential windowing** by default: it anchors to the last 1on1 date as a cutoff, so only new activity since the last meeting surfaces. If no prior 1on1 exists, it falls back to `--days 21`.

If the user explicitly requests a custom window (e.g., `/w-1on1 Bruno --days 30`), pass `--days N` to override.

Capture the JSON output. This is all the structured data the agent needs, including `carry_forward` items from the last 1on1's "Next time" section.

## Phase 1: Invoke 1on1-prep agent (pin to Opus)

Dispatch 1on1-prep as a `general-purpose` Agent with **`model: "opus"`** set explicitly on the Agent call, then have it invoke the `1on1-prep` skill via the Skill tool. The skill's frontmatter `model:` is NOT honored under `subagent_type: "general-purpose"` dispatch (see the "Model selection" note in `w-daily/SKILL.md`), so without the explicit parameter this prep silently inherits the session model instead of Opus.

Pass the JSON output from prep-1on1-data.py to the agent as context.
The agent should NOT search for interactions or grep for action items: all structured data is in the JSON.

The agent's job is now synthesis only:
1. Read the person's file in 04-People/ for role context
2. Read the prep JSON data (passed as context)
3. If past 1on1s exist in the JSON, read the last 1-2 for discussion detail (only if summaries are insufficient)
4. Synthesize talking points: carry-forward items first, then new interactions since last 1on1, then open actions
5. Create the meeting prep note in `00-Inbox/`

Report: new note path, open items, suggested topics.
