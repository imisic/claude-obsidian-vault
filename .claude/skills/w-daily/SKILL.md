---
name: w-daily
description: Create or open a daily note with ingestion and AI briefing. Master morning command that processes inbox and builds daily notes.
user-invocable: true
argument-hint: "[YYYY-MM-DD] [lite] [--upgrade-deferred]"
---

# Daily Note with Ingestion

**Arguments:** `$ARGUMENTS` may contain, in any order:
- a `YYYY-MM-DD` date → TARGET_DATE (the daily note created/opened; defaults to today). Referred to as TARGET_DATE below.
- the keyword `lite` → **lite mode**: process emails and docs fully, but defer transcripts to thin stub notes you choose per run. See Phase 1.7 (the choice) and Step 2.0 (execution).
- the flag `--upgrade-deferred` → **upgrade mode**: synthesize previously-deferred transcripts in place. This mode ignores the inbox. If present, run **Phase U** (end of this file) and stop; skip all other phases.

The default (`/w-daily` with no args, or just a date) is unchanged: a full run. `lite` is purely additive and only changes how transcripts are handled; emails, docs, daily notes, and briefings are identical to a full run.

A normal full run with several recorded meetings (at/above `ETA_DEFER_OFFER_MIN_TRANSCRIPTS` transcripts, default 3) now **pauses once** in Phase 1.7 to offer the same synthesize/defer choice, so a transcript-heavy morning is no longer silently committed to a long synthesis. Picking `all` reproduces the old full-run behavior. Light days (below the threshold, no `lite`) are unaffected and run straight through.

This is the master morning command. It ingests the inbox and builds the daily note.
Email pulling from OneDrive is handled by a Windows scheduled task (`_scripts/Pull-Emails.ps1`) that runs every 15 minutes. Emails are already in `00-Inbox/` by the time this runs.

## CRITICAL: Context budget rules

This command orchestrates. It does NOT do content work in the main context. Main-context minutes are the single largest cost. Every tool result you receive and every file you Write costs real wall-clock time and tokens; push work to scripts and agents that can write their own output.

**NEVER do in main context:**
- Read email bodies UNLESS inline processing (Phase 1.5a, ≤6 emails)
- Read `_db/entity-registry.json` (stub creation is handled by `create-stubs.py`)
- Read `_db/email-lookup.json` (entity resolution is done by `classify-inbox.py --resolve-entities`)
- Read full interaction notes to hand-author daily content: use `briefing_data[]` and scripts. `build-daily-briefings.py` may re-read final note bodies for action parsing; do not do that in main context.
- Do entity matching (done by script; agents receive pre-resolved data)
- **Write the processor agents' structured output JSON**. They now write their own `_db/email-out.json` / `_db/transcript-out.json` files. You only receive a tiny pointer. Re-emitting multi-thousand-line JSON via Write was the biggest time cost of the old pipeline; do not reintroduce it.
- **Hand-author daily note markdown**: `build-daily-briefings.py` generates it from `briefing_data[]` plus final note-body action parsing. The LLM only contributes optional sign-off and attention bullets for TARGET_DATE.

**ALWAYS do in main context:**
- Run scripts (classify, create-stubs, write-notes, build-daily-briefings)
- Read the **compact summary** from `classify-inbox.py` stdout (counts, file lists, batch plans, `is_backlog`)
- Read specific sections from `_db/manifest.json` only when building agent prompts (targeted reads)
- Dispatch processor agents; read their tiny pointer returns (≤200 chars each)
- Generate the tiny `_db/briefing-overrides.json` for TARGET_DATE (sign-off + attention bullets)

**Platform rules (Windows):**
- All pipeline scripts call `ensure_utf8_stdio()` from `utils.py`: no need for `PYTHONUTF8=1` prefix
- **Never use bash heredocs or echo-pipe for JSON**: breaks on quotes, special chars, and encoding. If you must stage JSON (inline Phase 1.5 only), use the `Write` tool to `_db/write-input.json`, then `write-notes.py --input _db/write-input.json`
- Use vault-relative paths for temp/intermediate files, never `/tmp/`
- `write-notes.py` note schema uses `source_files` (plural, list), not `source_file` (singular)

## Phase 0: Bootstrap & indexes

### First-run bootstrap
Check if `_db/entity-registry.json` exists and has content. If empty or missing:
1. Scan `04-People/*.md`, `03-Projects/*.md`, `07-Areas/06-Organization/**/*.md` for frontmatter
2. Build `_db/entity-registry.json` (schema in `.claude/rules/entity-matching.md`)
3. Create `_db/ingest-log.json` as `[]` if missing

### Index refresh (lightweight, conditional)

All indexes are **persistent and append-only**. They are maintained incrementally by Step 3.1b after each run. Phase 0 only does fast freshness checks, not full rebuilds.

```bash
# Backup critical DB files (daily snapshot, 7-day rotation)
python _scripts/backup-db.py --vault "$VAULT" &
# Incremental thread index: only scans notes newer than index mtime (fast no-op if nothing new)
python _scripts/build-thread-index.py --vault "$VAULT" --incremental &
# Email lookup: self-skips if registry hasn't changed since last build
python _scripts/build-email-lookup.py --vault "$VAULT" &
# Ingest-log audit: only runs if last audit was >7 days ago
bash _scripts/check-ingest-log.sh --if-stale "$VAULT" &
# Pull new Plaud recordings (OAuth via `plaud login`; skips silently if no auth)
python _scripts/pull-plaud.py --quiet &
# Archive today's calendar for recording-to-meeting matching
python _scripts/archive-calendar.py --vault "$VAULT" --quiet &
wait

# Phase 0.5: Enrich Plaud transcripts with calendar data (depends on pull + archive)
python _scripts/enrich-plaud-transcripts.py --vault "$VAULT" --quiet 2>&1 || true

# Phase 0.6: Plaud completeness check, warn if API has today's recordings that weren't pulled
# (catches slow-uploading recordings that fell behind the sync-state cursor)
python _scripts/check-plaud-completeness.py --vault "$VAULT" --date "$TARGET_DATE" 2>&1 || true
```

- `build-thread-index.py --incremental`: scans only new files since last index update. Full rebuild only on `--rebuild` or if index missing
- `build-email-lookup.py`: already skips rebuild when registry mtime < lookup mtime
- `check-ingest-log.sh --if-stale`: ghost entry cleanup + 90-day rotation. Runs weekly, not daily. Stamp in `_db/.last-audit`
- `pull-plaud.py --quiet`: pulls new Plaud recordings to `00-Inbox/` via `plaud_api` (OAuth from `plaud login`, legacy `.env` token as fallback). Incremental via `_db/plaud-sync.json`; exits 0 if no auth. Resolves speakers via `_db/plaud-speaker-map.json` (curated) then `_db/email-lookup.json`; surfaces unresolved/`Speaker N` in `_db/plaud-pull-summary.json`
- `archive-calendar.py`: persists calendar events to `_db/calendar-history.json` (7-day rolling window)
- `enrich-plaud-transcripts.py`: matches Plaud transcripts to calendar events by time overlap + subject similarity, rewrites headers with calendar attendees
- `check-plaud-completeness.py`: compares today's Plaud API recordings to local files (inbox + attachments). Prints a warning if API has more, listing the missing recording times so you can lower `_db/plaud-sync.json:last_sync_epoch_ms` and re-pull. Never fails the run

**Net effect**: For a typical morning run with a few emails, Phase 0 completes in <2 seconds. Plaud pull adds ~1-2s for API calls when new recordings exist, or <0.1s when nothing new.

## Phase 1: Classify and pre-process inbox (scripted)

### Step 1.1-1.5: All handled by classify-inbox.py

Move all processable files to staging first:
```bash
mkdir -p 00-Inbox/_processing
# Move all non-calendar, non-directory files
for f in 00-Inbox/*; do
  [[ -f "$f" ]] && [[ "$f" != *-calendar.json ]] && mv "$f" 00-Inbox/_processing/
done
```

Then run the classifier on the staging directory (with thread index for cross-batch matching and entity resolution):
```bash
python _scripts/classify-inbox.py --vault "$VAULT" --staging-dir "00-Inbox/_processing" --clean-bodies --sanitize-pii --thread-index "_db/thread-index.json" --resolve-entities
```

This **always** writes the full manifest to `_db/manifest.json` and outputs a **compact summary** to stdout. With `--sanitize-pii`, email addresses and phone numbers in email bodies are replaced with `[EMAIL-xxxx]`/`[PHONE-xxxx]` tokens before being stored in the manifest. Headers (From/To/CC) are NOT sanitized.

### Step 1.5.1: Create people stubs (scripted)

After classification, create stub files for unresolved entities:
```bash
python _scripts/create-stubs.py --vault "$VAULT"
```

This reads `_db/manifest.json` unresolved_entities, creates stub files in `04-People/`, and updates `_db/entity-registry.json`, `_db/email-lookup.json`, and `_db/sanitize-mappings.json`. Stubs resolve on the next ingestion run. Output is JSON with `created_stubs[]` and `registry_only[]` for the briefing ingestion summary.

### Compact summary (stdout, read into context)
Contains everything needed for orchestration decisions:
- `manifest_file`: path to full manifest
- `counts`: file type counts
- `emails[]`: compact: `{file, subject, date, pre_relevance, direction, vip_involved, output_filename, thread_id}`
- `transcripts[]`: compact: `{file, subject, date, meeting_type, stakes, est_minutes, output_filename}`
- `definitive_lows[]`: compact: `{file, subject, date, low_reason}`
- `pre_skipped[]`, `skipped_transcripts[]`, `manual_notes[]`, `manual_meetings[]`, `meeting_preps[]`, `docs[]`, `skipped[]`
- `thread_count`, `threads_with_existing[]`: threads that have >1 email or existing notes
- `batches[]`: `{batch_index, file_count, thread_count}`

### Full manifest (`_db/manifest.json`, read selectively)
Contains detailed per-email data needed for agent prompts:
- `email_manifest[]`: full parsed headers, pre-resolved entities, pre-generated frontmatter, output filenames, unresolved entities
- `transcripts[]`: full with resolved_attendees, frontmatter, output_filename
- `threads[]`: thread groups with existing_thread_notes
- `batches[]`: full file lists and thread_groups

**Read the compact summary. Read `_db/manifest.json` ONLY when building agent prompts. Use targeted reads for specific batch data, not the whole file.**

### Step 1.6: Handle script-resolved items (no agents needed)

**Definitive LOWs** (`definitive_lows[]`): These were pre-scored by classify-inbox.py using deterministic rules. For each:
1. Log to `_db/ingest-log.json` with `action: "skipped-low-relevance"`, include `subject`, `date`, `summary` (use `low_reason`)
2. Delete source file from `_processing/`
3. Include in `briefing_data` for ingestion summary count

**Skipped transcripts** (`skipped_transcripts[]`): Recovered/zero-duration duplicates filtered by script. For each:
1. Move source file to `_attachments/`
2. Log to `_db/ingest-log.json` with `action: "skipped-duplicate"`, `reason`

**Companion files** (`companion_files[]`): the `.md` and `.json` files that share a stem with a `.txt` transcript. Detected by `classify-inbox.py` and excluded from all other classification. For each:
1. Delete from `_processing/` (no note created, no log entry needed)
2. Include count in ingestion summary

**Empty meeting preps** (`meeting_preps[]` where `has_meeting_content: false`): Only handle here if no matching transcript exists (checked in Phase 4.3). If no transcript AND no content → delete + log as `skipped-empty` immediately.

## Phase 1.7: Upfront heads-up + synthesize/defer choice

`classify-inbox.py` adds an `eta` block to its compact summary: `{ full_minutes, lite_minutes, slow, transcript_count, defer_offer, breakdown[] }`, plus per-transcript `stakes` (substantive/low-stakes) and `est_minutes` in `transcripts[]`. This is the one point where the full inbox inventory is known **before** any slow Phase 2 agent runs. The estimates are deterministic and tunable (constants in `classify-inbox.py`); treat them as rough.

**Heads-up (both modes):** If `eta.slow` is true (full estimate over the ~2 min threshold), print one short line to the user before continuing, e.g.:
```
Inbox: 4 emails, 2 transcripts, 1 doc. Estimated full run ~13 min (lite ~2 min).
  - 1on1-jordan-lee      substantive  ~6 min
  - training-aws-basics  low-stakes   ~5 min
```
If `eta.slow` is false (a quiet morning under the threshold), print nothing extra and proceed. This keeps the default fast-path experience unchanged.

**Synthesize/defer choice (lite mode, or a heavy full run):** present this choice whenever **`lite` is in args OR `eta.defer_offer` is true** (the classifier sets `defer_offer` at/above `ETA_DEFER_OFFER_MIN_TRANSCRIPTS` transcripts, default 3). Below the threshold and not `lite`, skip this prompt and synthesize every transcript as before, so light days stay zero-friction. After the heads-up, present the transcripts and ask which to synthesize now, using the compact `transcripts[]` (subject, meeting_type, stakes, est_minutes):
```
Synthesize which transcripts now?  [all / none / substantive-only / pick]
```
- `none` → defer all; the daily note + emails land fast and you upgrade transcripts later with `--upgrade-deferred`. Best when you just need the daily note now (DEFER = all).
- `pick` → list transcripts with an index and choose which to synthesize now; chosen → SYNTH_NOW, the rest → DEFER. Best when only one or two meetings matter today.
- `all` → synthesize everything now (the old full-run behavior; SYNTH_NOW = all).
- `substantive-only` → SYNTH_NOW = transcripts with `stakes: substantive`, DEFER = the rest. Only saves time when the list actually shows `low-stakes` transcripts; with an all-substantive list it defers nothing (identical to `all`).

When presenting the choice, surface which transcripts (if any) are `low-stakes`, so the user can see at a glance whether `substantive-only` saves anything. There is no single "right" pick; it depends on what the user needs from this run.

Record SYNTH_NOW and DEFER (by transcript `file` / `output_filename`). Step 2.0 executes them. Ask this once, here, so the rest of the run is unattended. If there are zero transcripts, there is nothing to ask; proceed as a normal run.

## Phase 1.5: Inline processing (skip agent overhead where possible)

### Phase 1.5a: Inline email processing

**Conditions** (email-only decision, transcripts, docs, meeting preps are handled separately):
- ≤6 emails of **HIGH or MEDIUM** relevance in manifest (LOWs don't count, they're cheap log+delete operations, not content work)
- No complex threads (no thread has >3 emails)

To compute the count: read the compact summary's `emails[]` and count entries whose `pre_relevance` is `high` or `medium`. Treat `definitive_lows[]` as zero cost. They're handled in Phase 1.6 by a script-level log+delete regardless of inline vs agent path.

When conditions are met, process the HIGH/MEDIUM emails directly in the main context AND inline-handle the LOWs (log + delete), instead of spawning an email-processor agent. Transcripts, docs, and other content types are dispatched as agents in Phase 2 regardless. This decision only affects emails.

**Worked example**: 16 emails: 4 HIGH/MEDIUM, 12 LOW. Count = 4 ≤ 6 → inline. The 12 LOWs cost nothing more than the log+delete loop that Phase 1.6 runs anyway.

#### Inline email steps:

Entities, VIP, frontmatter, and filenames are **already resolved** in `_db/manifest.json` by `--resolve-entities`. Email bodies are **bundled** in `email_manifest[].cleaned_body`. No need to read `_db/email-lookup.json`, `_db/entity-registry.json`, or individual email files.

1. Read per-email data from `_db/manifest.json` → `email_manifest[]` (targeted read, filter to batch files). Each entry includes `cleaned_body`.
2. **For each email**, use `cleaned_body` from manifest (do NOT read files from `_processing/`):
   a. Finalize relevance (apply content waterfall, use `frontmatter.relevance` as default, which already includes VIP boost from `classify-inbox.py`). Do NOT re-apply VIP boost.
   b. Use `resolved_from`, `resolved_to`, `resolved_cc` wikilinks directly
   c. Use `frontmatter` dict as base, fill in `summary`, `thread-context`, `project`
   d. Use `output_filename` for the note file

3. **Build write-notes.py input**: construct JSON with `notes[]`, `log_entries[]`, `skipped_log_entries[]` per the email-processor return format. Each note MUST use `source_files` (plural, list) not `source_file` (singular). This field controls source deletion and transcript moves. Set `move_to_attachments: true` on transcript notes. Each note MUST include `briefing_data` (dict with `date`, `type`, `subject`, `summary`, `output_file`, `vip_involved`, `actions[]`, `decisions[]`). The builder script reads `briefing_data` from inside `notes[]` entries when processing inline write-input files.

4. **Write JSON to `_db/write-input.json`** using the Write tool (never bash heredocs, they break on quotes in body text). Then run:
   ```bash
   python _scripts/write-notes.py --vault "." --input "_db/write-input.json"
   ```
   Clean up: `rm _db/write-input.json` after.

5. **Validate `write-notes.py` output**: check `errors[]` is empty, `written[]` matches expected notes, `deleted[]` and `moved_to_attachments[]` match expected sources. If errors, log them and report in Phase 6.

6. **Build `briefing_data[]`** in same format as email-processor returns.

7. **Collect `unresolved_entities`** from manifest for Phase 3 stub creation.

**Budget exception**: Reading ≤6 HIGH/MEDIUM email `cleaned_body` fields (~2K tokens each = ~12K tokens) is acceptable vs agent spawn overhead. An email-processor agent dispatch costs ~5-8 min wall-clock minimum (Sonnet spin-up + full skill run + JSON serialization). Inline is ~30 seconds.

**Fall through**: If email conditions aren't met (>6 HIGH/MEDIUM emails or complex threads), emails go to Phase 2 agent dispatch.

### Phase 1.5b: Inline doc processing

**Conditions**: ≤3 documents in manifest.

For small doc batches, convert and create reference notes directly in main context instead of spawning a doc-processor agent. Conversion matches `doc-processor`: `markitdown` for PDF/DOCX/PPTX/XLSX, `defuddle` for HTML.

#### Inline doc steps:

1. Convert to markdown: `markitdown "filepath"` for PDF/DOCX/PPTX/XLSX; `defuddle "filepath"` for HTML.
2. Apply entity-matching (`.claude/rules/entity-matching.md`) to the converted content: resolve people/products/projects/markets to `[[wikilinks]]`.
3. Create reference note in `08-Reference/` with standard frontmatter (date, type: reference, source-file, summary).
4. Generate a 1-line summary from the converted content.
5. Delete original from `_processing/`.
6. Log to ingest-log.

**Fall through**: If >3 documents, dispatch doc-processor agent in Phase 2.

## Phase 1.9: Backlog detection

**Trigger**: `is_backlog: true` in the classify-inbox summary. This fires when either:
- >20 emails in manifest (vacation return, first-run bulk import), OR
- Processable content spans >4 days (wide date range despite modest count, the vacation-return case where total volume is low but files are spread out)

Read `is_backlog` and `backlog_reason` from the compact summary. No need to recompute.

When triggered, process email batches **sequentially** (not all in parallel) to avoid API concurrency issues:

1. Read per-batch data from `_db/manifest.json` → `email_manifest[]` filtered by batch file list
2. Dispatch ONE email-processor agent for batch N
3. Wait for completion, read its `_db/email-out.json` pointer, feed to `write-notes.py`, validate output
4. Report progress: "Batch N/M complete: X written, Y skipped, Z errors"
5. Proceed to batch N+1
6. After all email batches: dispatch transcript/doc agents in parallel

**Non-backlog runs**: use normal Phase 2 (parallel agents).

## Phase 2: Process remaining content in parallel

Dispatch agents only for content NOT already handled inline in Phase 1.5.

### Step 2.0: Transcript synthesize/defer split

Runs **whenever the DEFER set from Phase 1.7 is non-empty** (lite mode, or a heavy full run where the user chose to defer some transcripts). Use the SYNTH_NOW / DEFER sets recorded in Phase 1.7:
- **SYNTH_NOW transcripts**: dispatch normally via Step 2.1 below (same agent path, same quality).
- **DEFER transcripts**: do NOT dispatch an agent. Build a thin stub note for each (shape below). Step 2.2 writes them via `write-notes.py`, which also moves the raw transcript + companions to `_attachments/`.
- Emails and docs are unaffected and proceed exactly as in a full run.

If DEFER is empty (a normal light day, or the user picked `all`): skip this step; all transcripts go through Step 2.1.

**Deferred-stub output file** (one `notes[]` entry per deferred transcript; build each from its manifest `transcripts[]` entry). Write the whole thing to `_db/transcript-out-deferred.json`, the **same `{notes[], log_entries[]}` shape and naming family as a transcript agent's output file**. Do NOT call `write-notes.py` here: Step 2.2's batched `--inputs` pass writes this file alongside the SYNTH_NOW agent outputs, and Phase 5 reads the same files, so a deferred meeting flows through the identical path and appears (flagged) in the daily note. The file:
```json
{
  "notes": [{
    "output_path": "05-Interactions/<YYYY>/<output_filename>",
    "frontmatter": {
      "date": "<t.date>", "type": "meeting", "interaction-type": "meeting",
      "meeting-type": "<t.frontmatter.meeting-type>",
      "attendees": ["<from t.frontmatter.attendees>"],
      "summary": "<t.subject>",
      "status": "deferred",
      "deferred-source": "_attachments/<name>.txt",
      "recording-duration": "<t.frontmatter.recording-duration, if present>",
      "source-file": "<name>.txt"
    },
    "body_text": "> [!info] Transcript deferred, not yet synthesized. Raw file in `_attachments/<name>.txt`. Run `/w-daily --upgrade-deferred` to synthesize.\n",
    "source_files": ["00-Inbox/_processing/<name>.txt"],
    "move_to_attachments": true,
    "briefing_data": {
      "date": "<t.date>", "type": "meeting", "subject": "<t.subject>",
      "summary": "<t.subject>", "output_file": "<output_filename>",
      "vip_involved": ["<t.frontmatter.vip-involved, if any>"],
      "deferred": true, "actions": [], "decisions": []
    }
  }],
  "log_entries": [{
    "source-file": "<name>.txt", "action": "created",
    "output-file": "05-Interactions/<YYYY>/<output_filename>", "type": "meeting",
    "date": "<t.date>", "subject": "<t.subject>",
    "summary": "Deferred transcript (not yet synthesized)"
  }]
}
```
Rules:
- `write-notes.py` auto-moves the sibling `.json`/`.md` companions of the `.txt`, so list only the `.txt` in `source_files`.
- If the companion `.json` lists `annotations.screenshots[]`, add a `screenshot_files` array (the `00-Inbox/_screenshots/...png` paths) so they move to `_attachments/screenshots/<stem>/` for the eventual upgrade. Omit if none.
- `deferred-source` is the predicted `_attachments/<name>.txt`. If `_attachments/` already holds that name, `write-notes.py` moves the raw file to `<name>-2.txt` **and rewrites the stub's `deferred-source` to that actual path**, so it stays authoritative. (Phase U also verifies the resolved file's stem against `source-file` and re-globs on mismatch, protecting any stub written before that correction.)
- `briefing_data.deferred: true` makes `build-daily-briefings.py` flag the meeting `(deferred, not yet synthesized)` in the daily note.
- Put all deferred stubs in the one `_db/transcript-out-deferred.json` (a single `notes[]` array + `log_entries[]`). Step 2.2's pre-flight glob (`_db/transcript-out*.json`) and Phase 5's `--inputs` glob both pick it up automatically; Phase 7 cleanup removes it with the other `transcript-out-*.json` files.

**Key invariant: agents write their own output files. Master never re-serializes JSON.**
Processor agents write their structured output to `_db/email-out.json` / `_db/transcript-out.json` directly and return a tiny pointer (≤200 chars). The master reads the file and feeds it to `write-notes.py`. This eliminates the biggest time cost of the pipeline: typing multi-thousand-line JSON back through the main context.

### Step 2.1: Dispatch processor agents

Read per-batch/per-type data from `_db/manifest.json` (targeted reads: filter `email_manifest[]` by batch file list, read `transcripts[]`). Use this to build agent prompts. **One agent per batch/type.**

**How to dispatch (critical, wrong dispatch wastes a full turn):**

The processor skills (`email-processor`, `transcript-processor`, `doc-processor`) are NOT registered as `subagent_type` values in the Agent tool. They are **skills** that must be invoked via the Skill tool inside a `general-purpose` agent. Calling `Agent({subagent_type: "email-processor", ...})` errors with `Agent type 'email-processor' not found` and burns one round-trip.

The correct pattern:

```
Agent({
  subagent_type: "general-purpose",
  model: "sonnet",
  description: "Process N emails",
  prompt: "You are processing N emails for /w-daily ingestion. Invoke the `email-processor` skill via the Skill tool, then follow its SKILL.md to process the emails in the manifest. <task-specific instructions> ... Return ONLY the pointer JSON."
})
```

The agent itself calls `Skill({skill: "email-processor"})` once at the start, which loads the SKILL.md content and lets it follow the skill's instructions.

**Model selection (critical for performance):** processor agents must run on **Sonnet**, not the parent's Opus. Pass `model: "sonnet"` on every `Agent` tool call. Without this override, the agent inherits the parent's Opus and a 4-transcript batch can take 2-3× longer than necessary. The skill frontmatter `model:` field is not honored when dispatching via `subagent_type: "general-purpose"`. Only the explicit `model:` parameter on the Agent call wins.

**Model triage for transcript backlogs (quality-neutral speed win, added 2026-06-08):** Sonnet is the default. But all Sonnet agents share ONE throughput pool, so on a backlog (e.g. 8 transcripts dispatched at once) they contend. Wall-clock is bounded by *total Sonnet tokens ÷ throughput*, NOT by core count (14 cores still ran all 8 concurrently on 2026-06-08, yet the block took ~33 min because the agents throttled each other). Offload **low-stakes knowledge-transfer transcripts to Haiku** (`model: "haiku"`): Haiku draws from a separate, faster pool, so pulling 2-3 of N agents off Sonnet speeds up the *substantive* notes that remain. A transcript is low-stakes when its subject begins with a learning marker (`Lecture`, `Training`, `Webinar`, `Tutorial`, `Onboarding`, or a pure external product `Demo`) AND it is not a 1on1/steerco and Sam is a passive attendee (no decisions or Sam-owned actions expected). Everything else stays Sonnet. **Default to Sonnet when unsure.** This is a model swap, NOT a prompt slim: the Haiku agents still invoke the full `transcript-processor` skill, so the rules and output schema are identical and there is no second source of truth to drift. A misroute only swaps one capable model for another. It does not drop quality off a cliff, and the verbatim transcript stays in `_attachments/` for reprocessing if ever needed.

**email-processor agents** (one per batch, only if NOT inlined in Phase 1.5a):
- Agent reads `_db/manifest.json` ONCE. Manifest has everything: headers, entities, frontmatter, filenames, `cleaned_body` for each email, thread grouping
- Do NOT pass entity registry or email bodies in the prompt. It's all in the manifest
- Agent writes `_db/email-out.json` (the full structured payload) and returns `{"output_file": "_db/email-out.json", "status": "ready", "note_count": N, ...}`

**transcript-processor agents** (parallel when >2 transcripts, wall-clock matters):

A single Sonnet agent processes transcripts *sequentially* inside its turn. A 6-transcript batch on 2026-05-19 took 11 minutes wall-clock for that reason. Dispatch in parallel instead. Each transcript is independent (different file, different speakers, no shared state).

**Dispatch rule:**
- `transcripts.count ≤ 2` → ONE agent, single output file `_db/transcript-out.json` (orchestration overhead would outweigh parallelism here).
- `transcripts.count > 2` → ONE agent **per transcript**, ALL dispatched in a **single tool block** (one message, N `Agent` tool calls), no matter how many. **Do NOT manually split into waves of 6.** The runtime already caps live concurrency (min(16, cores-2)) and queues the overflow. Queued agents start the instant a slot frees, so there is no barrier. Manual wave-splitting forces a hard barrier: wave 2 can't start until the *slowest* agent of wave 1 finishes. On 2026-06-01, splitting 10 transcripts into 2 waves of ~5 made wall-clock ≈ 2 × (slowest agent ≈ 18 min) = ~36 min; one block would have been ≈ 1 × 18 min. **Wall-clock of one block = the single slowest agent; wall-clock of W manual waves = W × slowest. Always one block.**

Each agent gets ONE transcript's manifest entry **passed inline in the prompt as a filtered JSON slice** (do NOT tell the agent to read the whole `_db/manifest.json`: it carries every email's `cleaned_body` plus all transcript entries, a large irrelevant read repeated per agent; on 2026-06-01 a 741-byte truncated note still took ~17 min, latency dominated by fat reads + tool round-trips, not reasoning). Extract the slice with `jq '.transcripts[<i-1>]' _db/manifest.json` and paste it into the prompt. Each agent writes to `_db/transcript-out-{i}.json` where `i` is the 1-based transcript index. The filename pattern keeps per-agent outputs collision-free.

**Per-agent prompt skeleton (parallel path):**
```
Agent({
  subagent_type: "general-purpose",
  model: "sonnet",   // or "haiku" if this transcript is low-stakes per Model triage above
  description: "Process transcript i/N",
  prompt: "You are processing ONE transcript for /w-daily ingestion.
  Working dir: <vault>
  Invoke the `transcript-processor` skill via the Skill tool.
  Manifest entry (filtered to your one transcript, use this, do NOT read the full _db/manifest.json): <JSON slice from jq '.transcripts[i-1]'>
  Source file: <path>
  Companion .json (if MR transcript): <path or none>
  Write your full structured output (notes[], log_entries[], skipped_log_entries[]) to `_db/transcript-out-{i}.json` per SKILL.md schema.
  REQUIRED note shape (the master will reject anything else): each notes[] entry uses `output_path` (string, full vault-relative path, NOT output_file), `source_files` (a flat list of path STRINGS, NOT a list of dicts), `move_to_attachments: true`, `frontmatter` (dict), `body_text` (string), and a per-note `briefing_data` dict.
  CRITICAL JSON: escape every double-quote inside string values as \\\" and every newline as \\n. After writing, re-read your file and confirm it parses as valid JSON before returning (the two longest transcripts on 2026-06-01 shipped invalid JSON from unescaped quotes in body_text and had to be rebuilt).
  Return ONLY: {\"output_file\": \"_db/transcript-out-{i}.json\", \"status\": \"ready\", \"note_count\": N}"
})
```

**Common rules (both single and parallel paths):**
- Notes with `move_to_attachments: true` → `write-notes.py` moves originals to `_attachments/`
- Skipped duplicates that should be kept on disk (second-device captures of the same meeting) MUST have `move_to_attachments: true` on their `skipped_log_entries[]` entry. `write-notes.py` handles the move. The agent must NOT move files itself.
- **CRITICAL**: The agent prompt MUST explicitly require `log_entries[]` AND per-note `briefing_data` (a `briefing_data` dict inside each `notes[]` entry, NOT a top-level `briefing_data[]` array). Without `log_entries`, transcripts won't appear in `_db/ingest-log.json`. Without per-note `briefing_data`, transcripts won't appear in the daily note. Reference the transcript-processor SKILL.md output schema in the prompt.

**Aggregating parallel output:** the master receives N tiny pointer JSONs, then runs `write-notes.py --input` once per pointer file (in a loop or short bash for-loop). For Phase 5, pass all N files to `build-daily-briefings.py --inputs`.

**doc-processor agent** (only if NOT inlined in Phase 1.5b, i.e., >3 docs):
- Pass: document file list
- Agent returns: `{ created[], new_entities[], failed[] }`

### Step 2.2: Validate pointer, run `write-notes.py`

When transcripts were dispatched in parallel you have N pointer JSONs returned in one tool message.

**Order matters: run ONE pre-flight validation+normalization pass over ALL output files FIRST, fix everything, and only THEN run `write-notes.py`. Do not interleave validate→write→fail→patch→rewrite per file.**

Why this order (2026-06-01 lesson): `write-notes.py` writes the note file *before* it processes source-move/delete. If the input is off-schema in a way that crashes the source-move step (e.g. `source_files` is a list of dicts instead of strings), the note is already on disk, and because it crashed, you rerun, and collision handling writes a *second* copy (`-2`, then `-3`…). The run→fail→patch→rerun loop produced triplicate notes that then had to be hunted down and deleted. A single pre-flight pass that catches all schema issues up front avoids every rerun.

**Pre-flight pass (one script over all `_db/*-out*.json`):** for each file, for each `notes[]` entry, normalize and validate:
```python
import json, glob, os
for f in glob.glob("_db/email-out.json") + glob.glob("_db/transcript-out*.json"):
    try:
        d = json.load(open(f, encoding="utf-8"))   # (1) JSON PARSES, if not, SendMessage the agent to rebuild with escaped quotes/newlines; do NOT hand-repair
    except json.JSONDecodeError as e:
        print(f"INVALID JSON {f}: {e}"); continue
    for n in d.get("notes", []):
        if "output_path" not in n and "output_file" in n:        # (2) output_file → output_path (write-notes requires output_path; it does NOT alias this one)
            n["output_path"] = n.pop("output_file")
        sf = n.get("source_files", [])                            # (3) source_files must be a flat list of EXISTING path strings
        n["source_files"] = [ (x.get("path") if isinstance(x, dict) else x) for x in sf ]
        n["source_files"] = [p for p in n["source_files"] if p and os.path.exists(p)]
        if any(isinstance(x, dict) and x.get("move_to_attachments") for x in sf): n["move_to_attachments"] = True
        if "briefing_data" in d: pass
    if isinstance(d.get("briefing_data"), list): del d["briefing_data"]   # (4) strip deprecated top-level briefing_data[] array
    json.dump(d, open(f, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
```
Then run the `briefing_data` schema check (step 1b below) across all files. Only after every file passes do you run the write: a **single batched call** (`--inputs` concatenates all files into one pass: one interpreter startup instead of N, and collision handling runs across the whole set at once):
```bash
python _scripts/write-notes.py --vault "." --inputs $(ls _db/email-out.json _db/transcript-out.json _db/transcript-out-*.json 2>/dev/null)
```
You get one combined `{written[], deleted[], moved_to_attachments[], warnings[], errors[]}` to validate, not N. If `--inputs` is empty (no files matched) the glob yields nothing. Guard with `[ -n "$(ls ...)" ]` or skip when there's no agent output.

Per-pointer checks (apply during the pre-flight pass):

**1. Validate the pointer:**
- Agent returned a pointer with `"status": "ready"` and a matching `output_file` that exists on disk → proceed
- Pointer missing, `status != "ready"`, or the output file does not exist → treat whole batch as FAILED
  - Log each file with `action: "failed"` and reason: `"agent-no-output-file"`
  - Leave files in `_processing/` (picked up on next run)
  - Report failure in Phase 6
- Quick sanity check: optionally `jq length` the written file's `notes` array against `note_count`. If mismatched, log a warning but still proceed.

**1a. What write-notes.py auto-fixes vs what the pre-flight must fix.** `write-notes.py` defensively accepts and logs warnings for these. Do NOT pre-patch them:
- `content` field combining frontmatter+body → auto-split into `frontmatter` + `body_text`
- `body` field instead of `body_text` → aliased
- a `source_file`/`output_file` field meant as the hyphenated `source-file`/`output-file` metadata → normalized

But write-notes.py does **NOT** fix these. The pre-flight pass above handles them, because uncaught they either crash mid-write (leaving a collision duplicate) or silently drop the note:
- missing `output_path` (note used `output_file` for the path) → write-notes errors "missing required keys {'output_path'}"; the pre-flight renames it
- `source_files` as a list of dicts (`[{path, move_to_attachments}]`) → write-notes crashes with `TypeError ... not dict` *after* writing the note; the pre-flight flattens to path strings
- invalid JSON (unescaped quotes/newlines in `body_text`) → won't even parse; SendMessage the agent to rebuild

Beyond these known cases: don't pre-patch shapes that merely *look* off. If write-notes still reports an error after the pre-flight, then patch or SendMessage. The morning of 2026-05-19 burned ~3 min on an inline patch the script would have handled anyway.

**1b. Schema check, per-note `briefing_data` (required for daily note):**
Before feeding the agent's output to `write-notes.py`, verify every `notes[]` entry has a `briefing_data` object with non-empty `subject`. Run:
```bash
jq '[.notes[] | select((.briefing_data | type) != "object" or (.briefing_data.subject // "" | length) == 0) | .output_path]' <output_file>
```
Decision rule based on the failure count `K` and total notes `N`:
- `K == 0` → schema OK, proceed to step 2.
- `K ≤ 6` AND `K ≤ N/2` → **patch inline**. Read the agent's JSON, derive `briefing_data` from each missing note's `frontmatter` (date, subject, summary) + scan `body_text` for `- [ ]` actions and `## Decisions` lines, write back. This costs ~5 seconds vs ~60 seconds for a SendMessage roundtrip. Use this script template:
  ```python
  # patch script: fill missing briefing_data per note
  d = json.load(open(out_file))
  for n in d["notes"]:
      bd = n.get("briefing_data") or {}
      fm = n.get("frontmatter") or {}
      if not bd.get("subject"): bd["subject"] = fm.get("subject", "")
      if not bd.get("date"):    bd["date"]    = fm.get("date", "")
      if not bd.get("summary"): bd["summary"] = fm.get("summary", "")[:120]
      bd.setdefault("type", "email" if fm.get("type")=="email" else "meeting")
      bd.setdefault("vip_involved", fm.get("vip-involved", []) or [])
      bd.setdefault("output_file", os.path.basename(n["output_path"]))
      bd.setdefault("actions", [])    # or extract from body_text
      bd.setdefault("decisions", [])
      n["briefing_data"] = bd
  json.dump(d, open(out_file, "w"), indent=2, ensure_ascii=False)
  ```
- `K > 6` OR `K > N/2` → the agent has a systemic schema gap. SendMessage back to the same agent: "You omitted or left blank `briefing_data.subject`/`date`/`summary` on the following notes: `<list>`. Read your current `_db/<file>.json`, fill each per the SKILL.md schema (subject from `frontmatter.subject`), and Write the file back. Return `{status: \"ready\"}` when done." Re-run the schema check after.

Also reject any top-level `briefing_data[]` array. It's deprecated and would be double-counted by older script versions. If found, strip it inline (`del d["briefing_data"]`) and re-write the file.

**2. Feed the file directly to `write-notes.py`:**
```bash
python _scripts/write-notes.py --vault "." --input "_db/email-out.json"
python _scripts/write-notes.py --vault "." --input "_db/transcript-out.json"
```
`write-notes.py` handles: YAML frontmatter serialization, auto-stripping wikilinks from `summary` fields (with warning), file writing with collision handling, source file deletion/move-to-attachments, ingest-log updates with dedup guard. Returns JSON: `{ written[], deleted[], moved_to_attachments[], warnings[], logged, errors[] }`.

**Do NOT re-emit the agent's JSON via Write.** The agent already wrote the file; reading it back and re-writing it doubles the cost with zero benefit. The only reason to Write a new input file is when the master is doing inline processing (Phase 1.5a/b), where there's no agent output file yet.

**3. Validate `write-notes.py` output:**
- Check `errors[]` is empty. If not: log errors, report in Phase 6, but don't fail the run
- Check `warnings[]`: summary-sanitization warnings are non-fatal but worth surfacing
- Verify `written[]` matches expected note paths (accounting for collision renames)
- Verify `deleted[]` and `moved_to_attachments[]` match expected source files
- If `write-notes.py` fails entirely (non-zero exit): log all batch files as `action: "failed"`, leave in `_processing/`

### Step 2.3: Clean up staging

After ALL processors complete AND returns validated:
- Verify all expected source files were deleted by `write-notes.py`
- Verify transcript originals moved to `_attachments/`
- Any remaining files in `_processing/` = failures (log them with `action: "failed"`)
- Remove `_processing/` dir if empty

## Phase 3: Post-processing

### Step 3.1: People stubs (handled by script)

People stubs are now created by `create-stubs.py` in Step 1.5.1 (before agent dispatch). The script reads unresolved entities from the manifest and applies the stub creation threshold (≤5 recipients = stub file, >5 = registry-only). No LLM work needed here.

If agents report additional `new_entities` not in the manifest (rare), log them for the next run. Do NOT read `_db/entity-registry.json` in the main context for stub creation.

### Step 3.1b: Update thread index (append-only)

After notes are created, run the thread index updater with all created email note paths:
```bash
python _scripts/update-thread-index.py --vault "$VAULT" --notes path1.md path2.md ...
```

The script reads each note's frontmatter, extracts `conversation-id` and `subject`, normalizes, and appends to `_db/thread-index.json` with deduplication. No need to re-run `build-thread-index.py`.

### Step 3.2: Projects, flag only

If agents mention unrecognized topics, list in briefing as suggestions.

### Step 3.3: Ingest-log (mostly handled by write-notes.py)

`write-notes.py` writes ingest-log entries for all agent-processed content (emails, transcripts) with a dedup guard. This step only handles:
- Log entries for **manual notes** and **meeting preps** processed in Phase 4
- These are written directly to `_db/ingest-log.json` with the same dedup guard:
  - If existing entry has `action: "created"` AND its `output-file` exists on disk → skip
  - If existing entry has `action: "skipped-*"` AND new entry has `action: "created"` → replace
  - If existing entry has `action: "created"` AND new entry also has `action: "created"` → skip

### Step 3.4: Validate created notes (scripted)

Run `validate-notes.py` on all created note paths:
```bash
python _scripts/validate-notes.py path1.md path2.md ...
```

Returns JSON with pass/fail per note and specific issues (missing fields, empty summary, invalid values). Log validation issues. Don't block.

## Phase 4: Daily notes (date-scoped)

**Emails are date-specific.** Each email's briefing goes into the daily note for ITS date, not the processing date.

1. Group all created notes by their `date:` (from agent returns or quick frontmatter read)
2. For EACH date that has notes (including TARGET_DATE):
   a. Check if `01-Daily/YYYY/DATE.md` exists
   b. If not: create with frontmatter (date, type: daily, week number) + heading + `## Today's focus` + `## Notes`
3. For TARGET_DATE: if previous day's note exists, scan for unchecked `- [ ]` items to carry forward

**Consolidated thread emails**: When emails from different dates are consolidated into one thread note (per email-preprocessing Step 6), only the primary note generates a daily briefing entry (on its date). Thread-activity emails folded into the primary note do NOT generate separate daily note entries. They're covered by the primary note's Thread activity section.

### Step 4.1: Clean, condense, and merge manual notes

For each `manual_notes[]` file:
1. Read content, clean and condense (fix typos, structure, resolve names to wikilinks)
2. Merge into target daily note's `## Today's focus` and `## Notes` sections
3. Delete original, log to ingest-log

### Step 4.2: Clean, condense, and route manual meeting notes

For each `manual_meetings[]` file:
1. Read content, clean and condense body
2. Add `summary:`, `attendees:`, `source-file:` to frontmatter
3. Remove unfilled template scaffolding and Dataview queries
4. Move to `05-Interactions/YYYY/`
5. Delete original, log to ingest-log

### Step 4.3: Process meeting prep notes

Process AFTER transcripts complete. For each `meeting_preps[]`:

**Use `has_meeting_content` from manifest** (already checked by classify-inbox.py) to skip reading empty preps:

- If `has_meeting_content: false` AND no matching transcript → delete, log as `skipped-empty` (no file read needed)
- If `has_meeting_content: false` AND matching transcript exists → delete, log as `skipped-empty` (nothing to merge)
- If `has_meeting_content: true` AND matching transcript exists → read prep file, merge Meeting zone content into transcript note
- If `has_meeting_content: true` AND no matching transcript → read prep file, process as manual meeting note

Check if matching transcript note exists by: same date + person/attendees overlap.

## Phase 4.5: Manual capture from daily notes

For TARGET_DATE, route the daily note's `## Capture` section to its destinations. Runs after Phase 4 (manual notes / meeting preps merged) so any user-added captures from today's note get processed before Phase 5 rewrites the briefing block.

```bash
python _scripts/process-capture.py --vault "$VAULT" --date "$TARGET_DATE"
```

What it does:
- Reads `01-Daily/YYYY/$TARGET_DATE.md`
- Locates `## Capture` section
- For each non-comment, non-blank line:
  - `- [ ] description` → appended to `07-Areas/My-Tasks.md` `## Open` with `[[Sam-Rivera]]` as default owner (unless `[[SomeoneElse]]` typed explicitly), stamped with `[created:: $TARGET_DATE]` and `[source:: [[$TARGET_DATE]]]`
  - `- description` (no checkbox) → appended to the daily note's `## Notes` section
- Clears the Capture section (HTML comment template preserved)
- Creates `07-Areas/My-Tasks.md` on first capture if it doesn't exist

Output JSON: `{processed_lines, tasks_added, notes_added, errors[]}`. Log to Phase 7 report if `tasks_added > 0` or `notes_added > 0`.

**No-op when**:
- Daily note doesn't exist yet (Phase 4 creates it; this phase runs after)
- Capture section is empty or contains only comments

**Failure mode**: if `process-capture.py` errors, log and continue. Don't fail the run. The Capture section is left intact for retry.

## Phase 5: Write AI briefings (scripted)

Daily note briefings are assembled deterministically by `_scripts/build-daily-briefings.py` from the `briefing_data[]` the processor agents already produced, plus final written note bodies for action parsing. The master does NOT hand-author daily note markdown. That's the single largest time sink we eliminated.

### Step 5.1: Generate overrides (LLM work, minimal)

The only content the script can't fabricate well is:
1. The **sign-off line** for TARGET_DATE (one snarky sentence in Sam's voice, see `CLAUDE.md` "Vault prose voice")
2. The **Attention needed** bullets for TARGET_DATE (1-3 short risk/urgency items synthesized from the data)

Build a small overrides JSON only for TARGET_DATE. Other dates' notes use the default sign-off ("Morning scan complete.") and skip the Attention section.

Write to `_db/briefing-overrides.json`:
```json
{
  "2026-04-14": {
    "sign_off": "One line in vault voice",
    "attention_needed": [
      "Risk or open thread 1",
      "Risk or open thread 2"
    ]
  }
}
```

Omit fields that don't apply. Both are optional.

### Step 5.2: Run the builder

```bash
# Glob picks up both the single-agent file and any parallel per-transcript files
python _scripts/build-daily-briefings.py --vault "." \
  --inputs $(ls _db/email-out.json _db/transcript-out.json _db/transcript-out-*.json 2>/dev/null) \
  --target-date "$TARGET_DATE" \
  --overrides _db/briefing-overrides.json
```

The script:
- Aggregates all per-note `briefing_data` from the input files
- Groups by `date:`
- For each date: creates `01-Daily/YYYY/YYYY-MM-DD.md` if missing, or replaces the briefing block (between H1 and `## Today's focus`) if the note already exists
- Deterministic section order: Meetings → Key emails → Reference docs → Decisions made → Action items → Attention needed (TARGET_DATE only) → Ingestion summary → sign-off
- VIP markers (`**!**` boss-chain, `*` stakeholder) applied in Key emails and Action items
- **Action items are read from the FINAL written note bodies (post task-hygiene), not from `briefing_data.actions`**. The builder reuses `build-open-actions.py`'s parser, so any task demoted/stripped by `write-notes.py` hygiene never leaks into the daily. (Meetings, Key emails, and Decisions still come from `briefing_data[]`.) Falls back to `briefing_data.actions` only if a note file is unreadable.
- Action items split into **Sam-owned** and **Waiting on others**, each capped at 5; Decisions capped at 7; Key emails and Reference docs capped at 5. Overflow is surfaced as a "…and N more in source notes" / "…and N more in reference notes" line, never silently truncated (audit findings #1/#6, 2026-06-03).
- Reference docs (`08-Reference/`) render in their own compact section instead of being counted as meetings.
- Table pipes escaped inside wikilinks (`[[note\|link]]`) per Obsidian

Returns JSON with `written[]`, `updated[]`, `errors[]`. Validate no errors.

### Step 5.3: (intentionally empty)

Staging files (`_db/email-out.json`, `_db/transcript-out.json`, `_db/briefing-overrides.json`) are kept on disk until Phase 7. Deleting them here cost a real session ~2 minutes when a downstream step needed to re-read `briefing_data` and had to reconstruct it from notes on disk.

**Budget**: the LLM only produces ~200 tokens of overrides per TARGET_DATE. Everything else is scripted.

## Phase 6: Commit and push (optional)

If the vault is a git repo with a remote configured, commit the changes from this run and push. Runs before the user report so Phase 7 can include the result. If the vault is not under git, skip this phase entirely.

### Step 6.1: Commit pending changes

Stage only the vault **content** this run produces, never code/config, machine
churn, or unrelated edits. A blanket `git add -A` would sweep `.claude/` and
`_scripts/` edits, the rewritten `00-Inbox/*-calendar.json` snapshot, and any
root files into a `w-daily:` commit. Scope to the directories `/w-daily` writes:

```bash
cd "$VAULT"

# Clear a provably-stale git index lock before any index write. An interrupted
# background git process (commonly the Obsidian Git plugin's auto-backup) leaves
# .git/index.lock behind, which blocks every later git write. Judge staleness by
# AGE ALONE: in a small personal vault no legitimate index operation holds the
# index for minutes. (Do NOT add a `pgrep git` check: it is machine-global, so an
# unrelated git anywhere both false-positives and refuses to clear a genuinely
# stale lock in this repo.)
lock_status="ok"
if [ -f .git/index.lock ]; then
    if [ -n "$(find .git/index.lock -mmin +5 2>/dev/null)" ]; then
        rm -f .git/index.lock          # >5 min old: stale, safe to remove
        lock_status="stale-cleared"
    else
        lock_status="busy-deferred"    # <5 min: a write may be in progress
    fi
fi
echo "lock_status=$lock_status"

# busy-deferred: a write may be in flight. Skip the commit AND the push this run;
# the working tree is safe on disk and the next run retries. Never hard-fail.
if [ "$lock_status" != "busy-deferred" ]; then
    # Allowlist: only the content trees /w-daily touches (notes, stubs, attachments,
    # indexes, and inbox deletions of processed emails). Excludes .claude, _scripts,
    # _templates, _bases, and root files, those get committed explicitly.
    git add 00-Inbox 01-Daily 03-Projects 04-People 05-Interactions \
            07-Areas 08-Reference 09-Archive _attachments _db
    # Drop the calendar snapshot: rewritten every email pull, not a run output.
    git reset -q -- 00-Inbox/*-calendar.json

    # Commit only if the run actually staged something (the working tree may be
    # dirty with nothing but the excluded calendar file).
    if ! git diff --cached --quiet; then
        git commit -m "w-daily: $TARGET_DATE"
    fi
fi
```

### Step 6.2: Push if there are unpushed commits

```bash
unpushed=$(git rev-list --count origin/main..HEAD 2>/dev/null)
if [ "$lock_status" != "busy-deferred" ] && [ "${unpushed:-0}" -gt 0 ]; then
    push_output=$(git push origin main 2>&1)
    push_status=$?
else
    push_status=0  # nothing to push (or commit/push deferred), treat as success
fi
```

### Step 6.3: Categorize result for Phase 7

- `lock_status == stale-cleared` → include "git: cleared a stale index.lock" in the report
- `lock_status == busy-deferred` → include "git: commit/push deferred (index.lock busy, retries next run)"; skip the in-sync/pushed lines below
- `unpushed == 0` → include "git: in sync" in Phase 7 report
- Push succeeded → include "git: pushed N commits"
- Push failed → include the raw `push_output` in the report (e.g. SSH key not loaded, network unreachable)

**Never fail the skill run if push fails or the lock is busy.** Data is safe on local disk; both retry next run.

## Phase 7: Report to user

### Step 7.1: Clean up staging files

After the git push (so the commit captures any intermediate state if needed), remove the briefing staging files:
```bash
rm -f _db/email-out.json _db/transcript-out.json _db/transcript-out-*.json _db/briefing-overrides.json _db/write-input.json _db/briefings-input.json _db/briefings-extra.json
```

### Step 7.2: Report

- What was ingested (count + key items)
- Action items extracted
- Attention-needed flags
- Stub people created vs registry-only additions
- Validation warnings (if any)
- Daily notes created/updated (with dates)
- Confirm TARGET_DATE daily note is ready
- **Git sync status** (from Phase 6)
- Plaud completeness warning (if Phase 0.6 emitted one)

## Phase U: Upgrade deferred transcripts (`--upgrade-deferred`)

Runs **only** when `--upgrade-deferred` is in `$ARGUMENTS`. Ignores the inbox entirely. Synthesizes every `status: deferred` transcript stub in place, so each thin note becomes a full meeting note with no duplicate.

1. **Find deferred stubs**: `grep -rl "^status: deferred" 05-Interactions/` (or Glob + frontmatter read). If none, report "No deferred transcripts to upgrade." and stop.
2. **Resolve each raw transcript**: read the stub's `deferred-source` (an `_attachments/*.txt` path), then **verify the resolved file actually belongs to this stub** — its basename stem must start with the stub's `source-file` stem. Resolution order:
   - `deferred-source` exists on disk **and** its stem starts with the `source-file` stem → use it.
   - Otherwise (missing on disk, **or** a stem mismatch from a stub written before the attachment-collision correction) → glob `_attachments/` for the `source-file` basename and its `-N` variants (`<stem>.txt`, `<stem>-2.txt`, ...) and pick the matching file.
   - Still nothing → log the stub as `failed` (reason `deferred-source-missing` if no candidate file exists at all, `deferred-source-mismatch` if a file resolved but no variant matched the `source-file` stem), leave `status: deferred`, and continue with the rest. Never synthesize a non-matching transcript.
3. **Synthesize (agent, same as Phase 2)**: for each resolved transcript, dispatch a `transcript-processor` agent on Sonnet (or Haiku per the Model-triage rule). Pass the raw transcript path and the stub's known frontmatter (date, meeting-type, attendees). Instruct the agent to write `_db/transcript-out-upgrade-{i}.json` and to set each note's `output_path` to **the existing stub path** and `overwrite: true`. When >2, dispatch in one tool block (same parallel rule as Step 2.1). The synthesized frontmatter must NOT carry `status: deferred` or `deferred-source` (the note is now fully processed).
4. **Pre-flight + write** (same as Step 2.2): normalize/validate the agent outputs, then `python _scripts/write-notes.py --vault "." --inputs $(ls _db/transcript-out-upgrade-*.json 2>/dev/null)`. `overwrite: true` makes the synthesized note replace the stub in place (no `-2` duplicate). Leave `move_to_attachments` unset/false here: the raw transcript already lives in `_attachments/` and must stay (originals policy).
5. **Rebuild affected daily briefings**: collect the distinct `date:` of the upgraded notes and run `python _scripts/rebuild-daily-from-notes.py --vault "." --date <d1> --date <d2> ...` so the now-extracted actions and decisions appear and the `(deferred, not yet synthesized)` marker disappears.
6. **Reindex actions**: `python _scripts/build-open-actions.py --vault "."` so the new actions enter the `/w-1on1` and `/w-review` task surfaces.
7. **Commit (optional)**: if the vault is a git repo, commit per Phase 6's allowlist with message `w-daily: upgrade-deferred`.
8. **Cleanup + report**: remove the temp output files (`rm -f _db/transcript-out-upgrade-*.json`), then report which stubs were upgraded, which failed, and which dates' briefings were rebuilt.
