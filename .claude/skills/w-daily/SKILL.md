---
name: w-daily
description: Create or open a daily note with ingestion and AI briefing. Master morning command that processes inbox and builds daily notes.
user-invocable: true
argument-hint: "[YYYY-MM-DD]"
---

# Daily Note with Ingestion (v2)

**Arguments:** `$ARGUMENTS` may contain a `YYYY-MM-DD` date → TARGET_DATE (defaults to today).

Architecture: scripts orchestrate, models only produce content. Note-writing agents emit finished markdown notes into `_db/staged-notes/` (never JSON envelopes); `finalize.py` does all bookkeeping (validation, hygiene, moves, logs, indexes); briefings always rebuild from the notes on disk, so a rerun can never clobber a past day. Headless-ready: every step below is a script call or a standard Agent dispatch; an unattended run uses the default sign-off in Step 4.

**Main-context rules:** never read `_db/entity-registry.json`, `_db/email-lookup.json`, or raw sources. Email bodies come pre-cleaned and PII-sanitized as `cleaned_body` in the manifest; transcripts are dispatched to agents. Read `_db/manifest.json` only via targeted `jq` slices.

## Step 1: Prepare

```bash
python _scripts/prepare.py --vault "." --date "$TARGET_DATE"
```

One call runs index refresh + Plaud pull + calendar archive + transcript enrichment (internal parallelism), stages the inbox, classifies (`_db/manifest.json` + compact summary), and creates people stubs. Read the run-plan JSON it prints.

- `errors[]` non-empty → stop and report (no manifest, nothing to dispatch).
- `staged_leftovers[]` non-empty → a previous run died between dispatch and finalize. Tell the user; run `finalize.py` first if the leftovers look complete, else delete them (sources are still in `_processing/`, this run regenerates them under collision-safe names).
- `classify.eta.slow` true → print one heads-up line (counts + `full_minutes` + per-transcript breakdown). No pause.
- `completeness_warning` → include in the Step 7 report.

## Step 2: Dispatch content work

All content types run concurrently: send every Agent call in ONE message, then do inline email work while agents run.

**Transcripts** (one agent per transcript, `model: "sonnet"`, or `"haiku"` when its manifest entry says `stakes: low-stakes`):

For transcript `i` (0-based): extract the slice with `jq -c '.transcripts[i]' _db/manifest.json`, then dispatch:

```
Agent({
  subagent_type: "general-purpose",
  model: "sonnet",            // "haiku" for low-stakes
  description: "Note: <output_filename>",
  prompt: <contents of .claude/skills/w-daily/prompts/transcript-note.md>
          + "\n\n## Input\n```json\n" + <slice> + "\n```"
})
```

Read the template file once per run; append each slice. Agents return `NOTE: <staged path>` or `SKIP: <reason>`.

**Emails:** `email_manifest` count ≤10 with no thread wider than 3 → process inline: for each entry (targeted `jq` read incl. `cleaned_body`), apply `.claude/skills/w-daily/prompts/email-note.md` yourself and Write the staged note to `_db/staged-notes/<output_filename>`. Larger backlogs → batch agents (~10 emails each, `model: "sonnet"`): prompt = email-note.md + the batch's slices, agent applies the template per email independently and returns one `NOTE:`/`SKIP:` line per email.

**Docs:** ≤3 → inline (apply `prompts/doc-note.md` yourself, converting per its rules). More → one agent with doc-note.md + slices.

**Manual notes / manual meetings / meeting preps** (`classify.counts` shows them; rare): handle inline per `.claude/rules/ingestion.md` (merge manual notes into their daily note; clean manual meetings and Write them directly to `05-Interactions/YYYY/`; merge or discard prep notes). Collect their log entries for `--extra-log` below.

## Step 3: Finalize

Collect agent returns. Write `_db/skips.json` from the `SKIP:` lines: `[{"source_file": "<basename>", "reason": "...", "move_to_attachments": true}]` (`move_to_attachments` true for transcripts, false for emails). Every manifest entry needs either a staged note or a skips entry: when inline email work consolidates a thread into one note, add a skips entry per folded email (`"action": "merged"`, reason names the primary note). Then:

```bash
python _scripts/finalize.py --vault "." [--skips _db/skips.json] [--extra-log _db/extra-log.json]
```

finalize.py validates every staged note (schema, PII leaks, YAML), applies task hygiene + `[created::]`, moves notes into place (renaming if an agent corrected `meeting-type`), moves/deletes sources and companions per originals policy, logs everything to the ingest-log (including manifest `definitive_lows`/`pre_skipped`/`skipped_transcripts`), updates the thread index, and rebuilds `open-actions.json`. Read its output JSON:

- `quarantined[]` non-empty → report each with its reason; the sources stay in `_processing/` for the next run.
- An expected note missing (agent returned nothing) → same: report, source stays.
- Keep `touched_dates[]` for Step 5.

## Step 4: Author TARGET_DATE overrides

The only LLM-authored briefing content: a one-line sign-off in the vault voice and 0-3 `attention_needed` bullets (risks, gates, deadlines synthesized from today's notes). Write to `_db/briefing-overrides.json`:

```json
{"<TARGET_DATE>": {"sign_off": "...", "attention_needed": ["..."]}}
```

Omit fields that don't apply. Running headless: skip this step (briefe uses the default sign-off).

## Step 5: Briefings

```bash
python _scripts/briefe.py --vault "." --target-date "$TARGET_DATE" --touched <d1> <d2> ... --overrides _db/briefing-overrides.json
```

Pass finalize's `touched_dates[]` verbatim after `--touched` (space-separated, or one `--touched` per date; both parse). Omit the flag entirely when `touched_dates[]` is empty.

Runs the Capture routing for TARGET_DATE, then rebuilds each date's briefing block from the notes on disk. Existing `## Attention needed` bullets and custom sign-offs on other dates are preserved automatically. Validate `errors[]` is empty.

## Step 6: Commit and push

```bash
python _scripts/finish.py --vault "." --date "$TARGET_DATE"
```

Handles the stale-lock guard, allowlist staging, commit, and push categorization. Never fails the run; read its JSON for the report line (`busy-deferred` lock → commit deferred to next run; `publickey` → tell the user to run `ssh-add` for their git key).

## Step 7: Report, then clean up

Report: ingested counts + key items, actions extracted, attention flags, stubs created vs registry-only, quarantines/failures, deferred-commit or push status, Plaud completeness warning, confirmation that TARGET_DATE's daily note is ready. Then:

```bash
rm -f _db/briefing-overrides.json _db/skips.json _db/extra-log.json
```

## Failure posture

Any single item failing (bad staged note, agent silence) never blocks the rest: finalize quarantines it, its source stays in `_processing/`, and the next run picks it up. If `finalize.py` itself crashes, staged notes stay in `_db/staged-notes/` and Step 1's leftover check surfaces them tomorrow.
