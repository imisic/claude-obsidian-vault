---
name: w-project-status
description: Generate a status summary for a specific project or product. Aggregates recent interactions, decisions, actions, blockers, and OKR linkage.
model: claude-opus-4-6
user-invocable: true
argument-hint: "[Project or Product Name]"
allowed-tools: Read, Glob, Grep, Bash
---

# Project Status

Generate a status summary for **$ARGUMENTS**.

## Step 1: Refresh indexes

```bash
python _scripts/build-open-actions.py --vault "$VAULT" --skip-if-recent 300
```

## Step 2: Gather data

1. Find the project note in `03-Projects/` or product note in `07-Areas/06-Organization/Products/` matching the name
2. If not found, search with `Glob` and `Grep` for partial matches. If still nothing, report and stop
3. Read the project/product note
4. Find all interaction notes linked to this project in `05-Interactions/` (last 30 days) using `Grep`
5. Read `_db/open-actions.json` for actions mentioning this project
6. Check `07-Areas/06-Organization/Products/` for product files referencing this project
7. Check `07-Areas/` for OKR files referencing this project

## Step 2.5: External-repo enrichment (read-only)

If the project/product note has a `source-repo:` field **and** that path exists on disk, enrich the status from it (read-only, never write to the external repo). If `source-repo` is absent or not on this machine, skip this step and note in the output that the status reflects the vault only.

The repo (e.g. `project-repo`) and its exec-site are organized in two levels:
- **Level 1, lifecycle stages = the summary.** Built pages at `<source-repo>/exec-site/dist/<stage>/index.html` for `why, what, when, where, who, how` (Is the thesis sound? / What ships? / Timeline / Markets / Partners / How built). Convert HTML→text, `defuddle`/`markitdown` if installed, otherwise a stdlib `python3` regex tag-strip (no extra deps; verified to yield clean stage text). Read the stages relevant to the request, default `why`, `what`, `when`; for the umbrella `Project-Alpha` note, the Level 1 stages ARE the status.
- **Level 2, deep-dive library = drill-down.** Per-entity markdown at `<source-repo>/exec-site/dist/{products,competitors,partners}/<slug>.md`, plus source docs (`prd/**`, `research/**`, `strategy-brief.md`, opportunity briefs). Don't bulk-read, search first (`rg "<topic>" "<source-repo>"`), then read only what matches.

Product → Level 2 slug: map each product name to its `dist/products/<slug>.md` (and `prd/prd-<slug>.md` if present), drilling further only as needed.

If `exec-site/dist/` is missing (site not built on this machine), note "exec-site not built, run `npm run build` in `<source-repo>/exec-site`" and fall back to source docs: `strategy-brief.md` (the "At a glance" section + the product's section) + `README.md`. If `dist/` is older than the newest source doc, flag it as possibly stale.

Recent repo activity: `git -C "<source-repo>" log --since="30 days ago" --oneline`.

Fold program phase, product state, and recent repo activity into the synthesis below, attributed to the repo, alongside the vault's meetings and actions.

## Step 3: Synthesize

**Verification**: follow `.claude/rules/verification.md`. Every decision, action, blocker, and milestone must come from a note or index you actually read (vault or the external repo). Do not infer status the sources do not state; if a section has no support, omit it. If the project note was not found (Step 2) or the repo is absent (Step 2.5), say what you could not check rather than guessing.

Aggregate:
- Decisions made (from interaction notes and project decisions log)
- Open actions (from open-actions index)
- Blockers and risks mentioned
- Dependencies
- OKR linkage: how recent work maps to quarterly goals

## Step 4: Output

Print structured status directly to user:
- **Current state**: 1-2 sentences
- **Progress** (last 30 days): key milestones, decisions, deliverables
- **Open actions**: bulleted list with owners
- **Blockers / Risks**: if any
- **Next steps**: what needs to happen next

If the project situation is notably absurd or ironic, a closing dry observation is welcome. See `CLAUDE.md` personality traits.

Keep it under 2000 characters. List outcomes, not process.
