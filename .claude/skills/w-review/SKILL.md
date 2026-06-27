---
name: w-review
description: Unified review command for weekly, monthly, or custom period reviews. Analyzes vault activity and generates structured review notes.
user-invocable: true
argument-hint: "[weekly|monthly|monthly 2026-02|last N days|YYYY-MM-DD:YYYY-MM-DD|project:Name last 2 weeks]"
---

# Review

**Argument parsing:**
- `weekly` → last 7 days
- `monthly` → current month
- `monthly 2026-02` → specific month
- `last N days` → relative range
- `YYYY-MM-DD:YYYY-MM-DD` → explicit date range
- `project:Name last 2 weeks` → entity-scoped review
- No argument → defaults to `weekly`

## Step 1: Parse arguments from $ARGUMENTS

Determine:
- `period_type`: weekly, monthly, or custom
- `start_date` and `end_date` (YYYY-MM-DD)
- `scope`: optional entity filter (project:X, person:X, product:X)

## Step 1.5: Refresh open-actions index

Run `python _scripts/build-open-actions.py` to ensure `_db/open-actions.json` has current open AND completed action data. The review-agent uses this as the single source of truth for action completion state.

## Step 2: Invoke review-agent (pin to Opus)

Dispatch review-agent as a `general-purpose` Agent with **`model: "opus"`** set explicitly on the Agent call, then have it invoke the `review-agent` skill via the Skill tool. The skill's frontmatter `model:` is NOT honored under `subagent_type: "general-purpose"` dispatch (see the "Model selection" note in `w-daily/SKILL.md`), so without the explicit parameter this synthesis silently inherits the session model instead of Opus. Pass the parsed parameters; the agent analyzes vault content and returns structured review content.

The review includes a `## Demoted last week` section listing tasks the forgettability filter auto-demoted during the period (read from `_db/open-actions.json` → `demoted_actions[]`). The section is omitted when empty.

## Step 3: Create or print the review

**For weekly reviews:**
1. Calculate ISO week number from the period
2. Create `01-Daily/YYYY/YYYY-WXX-weekly.md` with frontmatter:
   ```yaml
   type: weekly-review
   week: XX
   period-start: YYYY-MM-DD
   period-end: YYYY-MM-DD
   ```
3. Insert the review-agent's output as the note body

**For monthly reviews:**
1. Create `01-Daily/YYYY/YYYY-MM-monthly.md` with frontmatter:
   ```yaml
   type: monthly-review
   month: YYYY-MM
   period-start: YYYY-MM-DD
   period-end: YYYY-MM-DD
   ```
2. Insert the review-agent's output as the note body

**For custom ranges / entity-scoped reviews:**
1. Print the review inline (no file created)
2. If the user says "save this", create a note with appropriate naming

## Step 4: Report to user
- Period covered
- Key stats: interactions analyzed, actions found, OKR status
- Link to created note (if applicable)
- Top attention items
