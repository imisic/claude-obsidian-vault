---
name: w-task-audit
description: Audit and clean up action items across interaction notes. Fixes missing delegated-by tags, removes noise tasks from group meetings, and reports task health.
user-invocable: true
argument-hint: "[--fix]"
---

# Task Audit

Scan all interaction notes for action item hygiene. Run periodically to catch tasks that are missing `[delegated-by::` tags or shouldn't be tasks at all.

**Default**: dry run (report only). Pass `--fix` as argument to apply changes.

## Phase 1: Run audit script

```bash
python _scripts/audit-tasks.py --vault "$VAULT" $ARGUMENTS
```

Supported flags:
- `--fix`: apply changes (default: dry-run)
- `--backfill-created`: stamp `[created::]` on tasks lacking it
- `--forgettability`: apply forgettability filter to Sam-owned tasks (demotes those without time horizon / deliverable / small-ask verb). Use for one-shot backfill of existing tasks.

The script applies these rules:
- **1on1 meeting** + owner != Sam → auto-add `[delegated-by:: [[Sam-Rivera]]]`
- **Sent email** + owner != Sam → auto-add `[delegated-by:: [[Sam-Rivera]]]`
- **Large meeting** (>5 attendees) + owner != Sam + no delegated-by → convert to plain text (remove checkbox)
- **Small meeting** (≤5 attendees) + owner != Sam → flag for manual review

## Phase 2: Handle review items

Only runs if `$ARGUMENTS` includes `--fix` AND the script reported items needing manual review.

For each flagged item:
1. Read the specific note file (path is in the script output)
2. Find the `## Actions` section and the flagged line
3. Read the `## Discussion` section (or body) for context on who initiated the action
4. Determine who asked for it:
   - Sam asked or instructed someone → add `[delegated-by:: [[Sam-Rivera]]]`
   - Someone committed something TO Sam (owed to Sam) → keep checkbox as-is, no `delegated-by`
   - Third party assigned by someone else, Sam was just present → remove the `- [ ]` checkbox, replace with `- ` (plain text). This is noise, not Sam's action.
5. Apply the edit to the file

If context is ambiguous (can't tell who initiated), skip the item and list it in the Phase 4 report as "unresolved". Don't guess.

**Budget**: Process up to 10 review items per run. If more than 10, process the first 10 and report "N remaining, run again to continue."

## Phase 3: Rebuild indexes

After any fixes:
```bash
python _scripts/build-open-actions.py --vault "$VAULT"
```

## Phase 4: Report

Report to user:
- How many tasks scanned
- How many delegated-by tags added
- How many noise tasks removed
- How many still need review (if any)
- Current open task count
