# Vault System Specification

Single reference document describing how the entire system works. **Keep this updated** when changing skills, rules, templates, configs, or data flows.

---

## Architecture Overview

This is an Obsidian-based personal knowledge management system for a knowledge worker who runs projects and meetings. It automates email ingestion, document processing, meeting management, and periodic reviews through a modular skill-based architecture running on Claude Code. A product-management preset (products, markets, segments, OKRs, steering-committee meetings) is optional and toggled during `/w-setup`. Throughout this document the owner is referred to by the example persona "Sam Rivera" at the fictional "Acme Corp". Replace these with your own details when you adopt the vault (run `/w-setup`, or see the repository README's Configuration section). The repository ships with a seeded, cross-linked **example dataset** built around this persona so each skill's output is visible out of the box; every seeded file carries an `[!example]` Obsidian callout, and raw input samples live in `_examples/inbox-samples/`. See the README's "Example content" section to explore or clear it.

**Core principles:**

- Skills orchestrate and execute, Rules define standards
- `/w-daily` is the single entry point for all ingestion
- Note-writing agents run on Sonnet (or Haiku for low-stakes transcripts); synthesis agents on Opus (quality)
- Entity registry (`_db/entity-registry.json`) is the single source of truth for linking
- Owner identity (slug, name, company, emails, timezone) lives in one place: the `OWNER CONFIG` block in `_scripts/utils.py`; every script imports it
- Action item checkboxes live ONLY in interaction/project/org notes. Daily notes render plain-text references from final note bodies without duplicating checkboxes
- Each email's briefing goes into the daily note for ITS date, not the processing date

---

## Vault Structure

```
Vault/
├── 00-Inbox/                    # Queue: emails, docs, manual notes
│   └── _processing/             # Staging dir during /w-daily run
├── 01-Daily/YYYY/               # Daily notes, weekly/monthly reviews
├── 03-Projects/                 # Projects + workstreams
├── 04-People/                   # Person files (stub or enriched)
├── 05-Interactions/YYYY/        # Email + meeting notes (year subfolders)
├── 07-Areas/                    # Dashboard cockpit: Dashboard.md (home note), My-Tasks.md, Open-Tasks.md, + operational Bases (Active-Projects, This-Week, Unprocessed, Recent-People)
├── 07-Areas/06-Organization/             # Products/, Markets/, Departments/, Teams/, Partners/, Segments/
├── 07-Areas/OKRs/               # Quarterly OKR files
├── 08-Reference/                # Converted documents (PDF/DOCX → markdown)
├── 09-Archive/YYYY-QN/          # Completed projects, past-quarter OKRs
├── _attachments/                # Raw transcripts, supporting files
├── _examples/                   # Sample raw inputs (copy into 00-Inbox/ to try the pipeline)
├── _templates/                  # Obsidian/Templater templates
├── _bases/                      # Reusable `this`-scoped Bases views embedded into entity notes (person/project/product/market/OKR)
├── _scripts/                    # Python + PowerShell automation scripts
├── _db/                         # entity-registry.json, ingest-log.json, sanitize-mappings.json
└── .claude/                     # Claude Code config
    ├── SYSTEM.md                # ← This file
    ├── rules/                   # Rule definitions (auto-loaded)
    └── skills/                  # Skill definitions
```

### Dashboard cockpit and Bases views

Obsidian Bases views split by scope. **Reusable, `this`-scoped views** live in `_bases/` and are embedded into entity notes to render that entity's slice (`person-interactions`, `project-interactions`, `okr-projects`, `product-overview`, `market-overview`). **Standalone, global cockpit views** live in `07-Areas/` next to the home note (`Active-Projects`, `This-Week`, `Unprocessed`, `Recent-People`), mirroring how they surface in the file tree.

`07-Areas/Dashboard.md` is the home note: set the Homepage plugin to open it. It is ordered action-first: an inline Dataview task panel (open, due-dated items, overdue first) leads, then the triage and project Bases, with recent-activity and people as secondary panels below the fold. The two task notes carry the full pile. `Open-Tasks.md` is a Dataview `TASK` aggregator over every open `- [ ]` in the vault (read-only; checkboxes stay in their source notes); `My-Tasks.md` is the personal capture bucket written by `process-capture.py`. The cockpit is presentation only: no script reads or writes the Bases, and the views populate from whatever notes exist (the seeded example data makes them non-empty out of the box). Bases needs the core Bases plugin enabled; `This-Week`'s live 7-day tab reads empty against the frozen March-2026 demo data and fills in once real interactions are ingested.

---

## Data Flow: Email Ingestion

```
Windows Scheduler (every 15 min)
    → Pull-Emails.ps1 copies from OneDrive EmailCapture → 00-Inbox/
    → Sent emails get SENT- filename prefix
    → Calendar JSON copied from EmailCapture/Calendar/ (overwrite, not move)

A meeting recorder or transcription tool (optional, bring your own)
    → Records and transcribes meetings; Plaud NotePin is supported out of the box
    → Saves a structured transcript with meeting metadata to 00-Inbox/

User runs /w-daily
    → Step 1 (prepare.py): single pre-dispatch entry point. One call runs index refresh
                (thread-index incremental, email-lookup self-skips if registry unchanged),
                optional Plaud pull + calendar archive + transcript enrichment, stages the inbox
                into _processing/, then classifies via classify-inbox.py (parse headers, clean bodies,
                sanitize PII (emails/phones → tokens), resolve entities to wikilinks+VIP,
                pre-generate frontmatter+filenames, group threads, detect duplicates, plan batches,
                compute ETA) and creates person stubs via create-stubs.py (also resurrects any
                previously-archived people who reappear). Full manifest → _db/manifest.json;
                one compact run-plan JSON → stdout (counts, batch plan, ETA, and any staged-note
                leftovers from a prior crashed run).
    → Step 2: Dispatch content work. Note-writing agents apply the prompt templates in
                .claude/skills/w-daily/prompts/ and Write finished markdown notes (YAML + body)
                into _db/staged-notes/<output_filename> (never JSON envelopes):
                ├── transcripts: one agent each (Sonnet, or Haiku when the manifest marks it
                │     low-stakes), prompt = transcript-note.md + the manifest slice
                ├── emails: ≤10 with no wide thread → applied inline by the master; larger backlogs
                │     → batch agents (~10 each) using email-note.md
                └── docs: ≤3 → inline via doc-note.md; more → one agent with doc-note.md + slices
                Manual notes / manual meetings / meeting preps are handled inline per ingestion.md.
    → Step 3 (finalize.py): deterministic post-pass, manifest-driven. Validates each staged note
                (schema, PII leaks, YAML), applies task hygiene + [created::], moves notes into
                05-Interactions/YYYY/ or 08-Reference/ (renaming on an agent-corrected meeting-type),
                moves/deletes sources + companions per originals policy, writes the ingest-log,
                updates the thread index, and rebuilds open-actions.json. Folds the old
                validate-notes.py and update-thread-index.py.
    → Step 4: Author per-date briefing overrides (the sign-off line + `## Attention needed` bullets;
                an unattended run uses the default sign-off).
    → Step 5 (briefe.py): rebuild each touched day's briefing from ALL of that date's notes on disk
                (imports build-daily-briefings.py as its render library), preserving the LLM-authored
                Attention-needed + sign-off. A rerun can never clobber a past day.
    → Step 6 (finish.py): git phase. Stale-lock guard, allowlist-stage the content trees, commit only
                when something is staged, push only when there are unpushed commits. Push failure is
                categorized, never fatal.
    → Step 7: Report to user, clean up staging.
```

**Outputs:** Interaction notes in `05-Interactions/YYYY/`, reference docs in `08-Reference/`, person stubs in `04-People/`, daily notes in `01-Daily/YYYY/`

**Run mode:** `/w-daily [YYYY-MM-DD]` runs the full pipeline (defaults to today). When `classify-inbox.py` estimates a slow run (transcript-heavy), Step 1 prints a one-line ETA heads-up (counts + estimated minutes + per-transcript breakdown); it does not pause. See `.claude/skills/w-daily/SKILL.md`.

---

## Skills

### User-Invocable (7)

| Skill            | Command                                       | What it does                          | Creates                               |
| ---------------- | --------------------------------------------- | ------------------------------------- | ------------------------------------- |
| w-setup          | `/w-setup`                                    | Setup wizard: interview → configure vault | utils.py owner block, registry, notes |
| w-daily          | `/w-daily [YYYY-MM-DD]`                        | Master ingestion + daily note builder | Daily notes, interaction notes, stubs |
| w-review         | `/w-review weekly\|monthly\|last N days\|...` | Period review with vault analysis     | Weekly/monthly review notes           |
| w-1on1           | `/w-1on1 [Person Name]`                       | 1on1 meeting prep                     | Pre-populated meeting note            |
| w-project-status | `/w-project-status [Name]`                    | Project/product status summary (Opus) | Inline report (no file)               |
| w-prep           | `/w-prep [person/topic] [last wk]`            | Conversation prep (fwd) or "what I did" recap (retro), cross-repo (Opus) | Inline brief; optional 00-Inbox prep note |
| w-task-audit     | `/w-task-audit [--fix]`                       | Action item hygiene audit             | Inline report, optional fixes         |

### Note-writing Agents (Sonnet / Haiku)

These are standard `general-purpose` Agent dispatches (not context forks). Each is driven by a prompt template in `.claude/skills/w-daily/prompts/`; the agent applies the template to its manifest slice and Writes a finished markdown note (YAML frontmatter + body) into `_db/staged-notes/<output_filename>`, returning a one-line `NOTE: <path>` or `SKIP: <reason>`. No JSON envelopes: `finalize.py` picks the staged notes up. The master handles small inline batches (≤10 emails, ≤3 docs) itself with the same templates instead of dispatching an agent.

| Template            | Model                        | Input (manifest slice)                                   | Output                                                            |
| ------------------- | ---------------------------- | -------------------------------------------------------- | ---------------------------------------------------------------- |
| `transcript-note.md`| Sonnet (Haiku if low-stakes) | Resolved attendees, frontmatter + PII-tokenized transcript body (`agent_file`) | Staged meeting note in `_db/staged-notes/`                        |
| `email-note.md`     | Sonnet (batch) / inline      | Cleaned body, resolved entities, frontmatter             | Staged email note (or `SKIP:` for LOW/merged)                    |
| `doc-note.md`       | Sonnet / inline              | Document file path + conversion rules                    | Staged reference note in `_db/staged-notes/`                     |

### Internal Synthesis Agents (Opus)

| Agent                | Input                        | Output                                     |
| -------------------- | ---------------------------- | ------------------------------------------ |
| review-agent         | Period dates, optional scope | Structured review markdown                 |
| 1on1-prep            | Person name                  | Meeting note + open items + talking points |

---

## Rules

All in `.claude/rules/` (auto-loaded into context).

| Rule file                 | What it defines                                                                                                                                                   | Used by                                        |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| `ingestion.md`            | Pointer rule: `/w-daily` is the single ingestion entry point, and the three long procedures below must be read on demand before touching `00-Inbox/` content       | w-daily                                        |
| `entity-matching.md`      | Name/email → wikilink resolution, registry schema, domain → company mapping, owner detection, stub creation threshold, recipient parsing                          | classify-inbox.py, create-stubs.py             |
| `vip.md`                  | VIP tier definitions (boss-chain/stakeholder/team), relevance boost rules, frontmatter tags, briefing markers                                                     | classify-inbox.py, note-writing agents, w-daily |
| `obsidian-conventions.md` | Vault structure, frontmatter formats, action item rules (single source of truth), linking conventions, periodic note formats, archive policy                      | All skills                                     |
| `verification.md`         | Anti-fabrication, no-false-absence, and verify-don't-trust rules for entity matching, extraction, and synthesis. Distilled into each note-writing prompt template's "No fabrication" section (agents do not read rules at runtime); referenced by the synthesis skills | Note-writing agents, review-agent, 1on1-prep, w-project-status |

### Lazy-loaded ingestion references

These are NOT rules and are NOT auto-loaded. They live in `.claude/skills/w-daily/references/` and are read on demand, as `.claude/rules/ingestion.md` instructs. Together they are roughly 590 lines that a normal script-driven run never needs.

| Reference file            | What it defines                                                                                                                                                   | Used by                                        |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| `ingestion.md`            | Content type detection, routing, meeting/reference frontmatter, file naming, originals policy, manual note processing, logging                                    | classify-inbox.py, finalize.py, w-daily        |
| `ingestion-email.md`      | Email pulling, Power Automate format, email parsing rules, email frontmatter schema, tiered routing                                                               | classify-inbox.py, w-daily                     |
| `email-preprocessing.md`  | Body cleaning (Teams footers, disclaimers, safe links), duplicate detection, relevance scoring (HIGH/MEDIUM/LOW waterfall), thread identification + consolidation | classify-inbox.py, email-note.md               |

---

## Content Types & Routing

| Type                        | Detection                                                            | Destination                                                   | Frontmatter type         |
| --------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------- | ------------------------ |
| Email                       | `.txt` with From/Subject/Date headers; `.eml`/`.msg`                 | `05-Interactions/YYYY/`                                       | `type: email`            |
| Manual note                 | `.md` with `type: manual-note` frontmatter                           | Merged into `01-Daily/YYYY/`                                  | (merged, not standalone) |
| Manual meeting note         | `.md` with `type: meeting` + `interaction-type: meeting` frontmatter | Clean/condense → `05-Interactions/YYYY/`                      | `type: meeting`          |
| Meeting prep note           | `.md` with `type: meeting` + `meeting-prep: true` frontmatter        | Merge with transcript or standalone → `05-Interactions/YYYY/` | `type: meeting`          |
| Structured transcript       | `.txt` with MeetingSubject/MeetingDate/Attendees headers             | `05-Interactions/YYYY/`                                       | `type: meeting`          |
| Generic transcript          | Timestamps + speaker labels                                          | `05-Interactions/YYYY/`                                       | `type: meeting`          |
| Meeting notes (external)    | Attendees/Agenda/Minutes keywords (no frontmatter)                   | `05-Interactions/YYYY/`                                       | `type: meeting`          |
| Document                    | PDF/DOCX/PPTX/XLSX/HTML                                              | `08-Reference/`                                               | `type: reference`        |

### Email Relevance Tiers

| Tier   | Result                                        | Signals                                                                                                           |
| ------ | --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| HIGH   | Full interaction note                         | >5 lines original text, decision/pushback/delegation language, data/metrics, HR topics, >3 recipients + substance |
| MEDIUM | Condensed note (frontmatter + 1-line summary) | Short reply to substantive thread, delegation without detail, forward with commentary                             |
| LOW    | Log only (no note)                            | Meeting invite template, <3 lines logistical, FYI forward, acknowledgment, admin/personal                         |

After content-based scoring, a **VIP relevance adjustment** (Step 3.5) applies: boss-chain in From/To boosts LOW→MEDIUM, MEDIUM→HIGH; boss-chain in CC boosts LOW→MEDIUM; stakeholder in From/To boosts LOW→MEDIUM. Team members get no boost (high-volume daily collab). See `.claude/rules/vip.md`.

---

## Database Files

### `_db/entity-registry.json`

Source of truth for entity linking. Schema:

```json
{
  "people": [{ "link": "[[Name]]", "name", "aliases": [], "emails": [], "company", "stub": true|false, "vip": "boss-chain|stakeholder|team" }],
  "products": [{ "link", "name", "aliases": [] }],
  "projects": [{ "link", "name", "aliases": [] }],
  "markets": [{ "link", "name", "aliases": [] }],
  "segments": [{ "link", "name", "aliases": [] }],
  "teams": [{ "link", "name", "aliases": [] }]
}
```

- Auto-built on first `/w-daily` run (Phase 0 bootstrap)
- Updated after each ingestion (new people, emails)
- People with `"stub": false` = mass CC recipients, no stub file
- People with `"vip"` field = VIP tier (boss-chain/stakeholder/team). Absent = non-VIP

### `_db/thread-index.json`

Fast lookup index for cross-batch thread matching. Maps ConversationId and normalized subject to existing interaction note paths. **Persistent and append-only**, maintained by Step 3.1b after each run. Phase 0 runs `build-thread-index.py --incremental` which only scans notes newer than the index (fast no-op most days). Full rebuild on `--rebuild` or if index missing.

### `_db/email-lookup.json`

Lightweight email→wikilink+VIP lookup extracted from entity-registry.json (~10KB vs the full registry). Used by `classify-inbox.py` during entity resolution (and by the Plaud speaker resolver) for fast lookups. **Self-skips rebuild** if registry mtime < lookup mtime (no work if registry hasn't changed since last build).

### `_db/person-index.json`

Person→interactions index built by `build-person-index.py`. Maps each person slug to their interaction history (date, type, summary, role) from frontmatter only, no body reads. Used by `/w-1on1` for fast 1on1 prep. Includes per-person meta (total interactions, last interaction, last 1on1).

### `_db/open-actions.json`

Action items extracted from `05-Interactions/`, `03-Projects/`, and `07-Areas/06-Organization/` (Partners/Products hub pages, a legitimate task surface, indexed since 2026-06-03 so checkboxes there aren't a silent second task truth) by `build-open-actions.py`. Structure: `{ total_open, total_completed, by_owner, by_person, completed_actions }`. `by_owner`/`by_person` index open (`- [ ]`) items only (1on1 prep compatibility). `completed_actions` is a flat list of checked (`- [x]`) items sorted by note_date descending. Used by `/w-1on1` for open action lookups and `/w-review` for action completion state (single source of truth: reviews never derive completion from daily note text).

### `_db/ingest-log.json`

Audit trail of all processed files. Each entry: `{ timestamp, source-file, action, output-file, type, subject, date, summary }`.

- Actions: `created`, `skipped-low-relevance`, `skipped-duplicate`, `skipped-already-processed`, `merged`, `merged-with-transcript`, `skipped-empty`, `failed`
- Ghost entry cleanup: on startup, remove `action: "created"` entries where `output-file` doesn't exist

### `_db/sanitize-mappings.json`

Bidirectional PII token mappings. Used by `classify-inbox.py --sanitize-pii` and `create-stubs.py`.

- `emails`: `{email_address → "[EMAIL-xxxx]"}` -- email-to-token
- `phones`: `{phone_number → "[PHONE-xxxx]"}` -- phone-to-token
- `token_to_pii`: `{token → original_value}` -- reverse lookup for manual inspection
- Auto-grows as new PII is discovered during ingestion. Never shrinks.

---

## Templates

Located in `_templates/`. Templater plugin auto-applies based on folder.

| Template             | Folder trigger                | Purpose                                                              |
| -------------------- | ----------------------------- | -------------------------------------------------------------------- |
| `daily-note.md`      | `00-Inbox`                    | Manual daily note (type: manual-note, stays in inbox for processing) |
| `person.md`          | `04-People`                   | Person stub/file                                                     |
| `project.md`         | `03-Projects`                 | Project file                                                         |
| `product.md`         | `07-Areas/06-Organization/Products`    | Product file                                                         |
| `market.md`          | `07-Areas/06-Organization/Markets`     | Market/country file                                                  |
| `team.md`            | `07-Areas/06-Organization/Teams`       | Team file (Group/VS)                                                 |
| `segment.md`         | `07-Areas/06-Organization/Segments`    | Business segment                                                     |
| `department.md`      | `07-Areas/06-Organization/Departments` | Department file                                                      |
| `partner.md`         | `07-Areas/06-Organization/Partners`    | Partner org                                                          |
| `reference-doc.md`   | `08-Reference`                | Reference document                                                   |
| `email.md`           | (manual)                      | Email note                                                           |
| `meeting-1on1.md`    | (manual)                      | 1on1 meeting                                                         |
| `meeting-general.md` | (manual)                      | General meeting                                                      |
| `meeting-steerco.md` | (manual)                      | Steering committee                                                   |
| `meeting-sync.md`    | (manual)                      | Sync meeting                                                         |
| `async.md`           | (manual)                      | Async interaction (Slack/Teams thread)                               |
| `okr.md`             | (manual)                      | OKR file                                                             |
| `weekly-review.md`   | (manual)                      | Weekly review                                                        |
| `monthly-review.md`  | (manual)                      | Monthly review                                                       |
| `workstream.md`      | (manual)                      | Workstream file                                                      |

---

## Scripts

Located in `_scripts/`.

| Script                      | Runs                                              | What it does                                                                                                                                                                                                                                                                                                                                                                                                                                |
| --------------------------- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Pull-Emails.ps1`           | Windows Task Scheduler, every 15 min              | Copies emails from OneDrive EmailCapture → `00-Inbox/`, adds `SENT-` prefix to sent. Also copies calendar JSON from `EmailCapture/Calendar/`, and email attachments from `EmailCapture/Vault/Attachments/` → `00-Inbox/_email-attachments/` (pulled first, names made wikilink-safe; received emails under 120s old are deferred so their attachments can settle)                                                                                                                                                                                                                                                                                                |
| `Install-EmailPullTask.ps1` | One-time manual (elevated PS)                     | Registers the scheduled task                                                                                                                                                                                                                                                                                                                                                                                                                |
| `check-environment.py`      | Called by `/w-setup`; runnable anytime            | Doctor: reports optional tools (markitdown, defuddle, Plaud CLI) AND vault integrity (`_db/` present, `entity-registry.json` and `ingest-log.json` parse), each with a fix hint. Stdlib-only; `--json` for the skill, `--strict` exits 1 on a failed required check. Nothing in optional tools is required to start: PDF/HTML/images/text process with zero installs                                                                                                                                                                                                              |
| `apply-setup.py`            | Called by `/w-setup` Step 3                       | Deterministic writer for setup answers (`_db/setup-answers.json`): rewrites the marker-bounded `OWNER CONFIG` block in `utils.py`, builds `entity-registry.json` (owner + manager + VIPs + projects), repoints `bookmarks.json`, copies `.env` from example. Idempotent; prose edits are left to the `/w-setup` skill                                                                                                                          |
| `prepare.py`                | Called by `/w-daily` Step 1                       | Single pre-dispatch entry point. Sequences the former Phase 0 + Phase 1 work in one process (internal parallelism): index refresh (`build-thread-index.py`, `build-email-lookup.py`), backup, ingest-log audit, optional Plaud pull + calendar archive + transcript enrichment, inbox staging into `_processing/`, then `classify-inbox.py` and `create-stubs.py`. Prints one compact run-plan JSON (classify summary, stubs, completeness warning, staged-note leftovers, warnings/errors). Exits non-zero only if classification itself fails |
| `classify-inbox.py`         | Called by `prepare.py` (Step 1)                   | Full deterministic preprocessing: classification, header parsing, body cleaning (`--clean-bodies`), PII sanitization (`--sanitize-pii`), entity resolution (`--resolve-entities`), frontmatter+filename generation, thread grouping, duplicate detection, batch planning. Pre-scores definitive LOWs, filters recovered transcripts, checks meeting prep content. Bundles `cleaned_body` (sanitized) into manifest, and writes a PII-tokenized transcript working copy under `_db/agent-inputs/` (manifest `agent_file`). Computes an `eta` block (`full_minutes` + `slow` flag + `transcript_count` + per-item `breakdown`) and stamps each transcript with its `stakes` (substantive/low-stakes) for the ETA heads-up and Haiku routing of low-stakes recordings. Low-stakes detection normalizes the subject (strips a leading date/generic-meeting prefix) and matches a learning marker as a prefix or a narrow trailing demo/walkthrough noun. Correlates staged email attachments by receive-second (`attachment_stamp()` / `find_staged_attachments()`) and **promotes substantive ones** (`utils.is_promotable_attachment`: pdf/Office) from note-producing emails into `docs` manifest entries (`is_email_attachment`/`source_email`/`clean_stem`) so their content is converted to `08-Reference/`. Outputs full manifest to `_db/manifest.json` + compact summary to stdout |
| `create-stubs.py`           | Called by `prepare.py` (Step 1)                   | Reads manifest unresolved_entities, creates person stub files in `04-People/`, updates entity-registry.json, email-lookup.json, and sanitize-mappings.json. Applies stub threshold (≤5 recipients = file, >5 = registry-only). **Resurrects archived people**: if an inbound email matches a registry entry with `status=archived`, moves the file from `04-People/_archived/` back to `04-People/`, clears the status flag, and appends a `RESURRECT` row to `_db/people-archive-analysis.csv`. **Case-collision guard**: skips any unresolved entity whose filename collides case-insensitively with an existing person file (never appends a registry entry or writes the stub), surfacing it as `skipped_case_collision[]`. Prevents the case-insensitive-FS clobber where a transcript attendee slug like `Sam-Rivera` downcased to `sam-rivera` would overwrite the real `Sam-Rivera.md` |
| `finalize.py`               | Called by `/w-daily` Step 3                       | Deterministic post-pass, manifest-driven. LLM agents Write finished markdown notes into `_db/staged-notes/`; finalize validates each (schema, YAML, PII leaks), **applies `apply_task_hygiene()` per body line (stamps `[created::]`, auto-converts non-Sam tasks in large group settings to plain bullets, auto-adds `[delegated-by:: [[Sam-Rivera]]]` in 1on1s / small meetings / sent emails; VIP protection is per-task owner, not whole-note)**, moves each note to its `05-Interactions/YYYY/` or `08-Reference/` home (renaming on an agent-corrected `meeting-type`; `-2`/`-3` collision suffix), moves/deletes sources + companions per the originals policy (email attachments move to `_attachments/email/<stamp>/`; an `is_email_attachment` doc keeps its source so the parent email's move relocates the single raw file), updates the ingest-log with a dedup guard (including manifest `definitive_lows`/`pre_skipped`/`skipped_transcripts`), appends to the thread index, and rebuilds `open-actions.json`. Folds the former `validate-notes.py` (schema + body lint for raw `@mentions`, leaked Dataview, un-tokenized PII) and `update-thread-index.py`. The agent's frontmatter text is authoritative and kept verbatim except a surgical summary patch. Returns JSON per note (written / quarantined / moved / errors) |
| `check-ingest-log.sh`       | Called by `/w-daily` Phase 0                      | Removes ghost entries + 90-day rotation. Supports `--if-stale` to run weekly only (checks `_db/.last-audit`)                                                                                                                                                                                                                                                                                                                                |
| `_check_ingest_log_impl.py` | Called by `check-ingest-log.sh`                   | Python helper for the ingest-log audit: deduplicates entries by `source-file` (prefers `created` over `skipped`) and removes ghost `created` entries whose `output-file` no longer exists                                                                                                                                                                                                                                                   |
| `build-thread-index.py`     | Called by `/w-daily` Phase 0                      | Supports `--incremental` (only scan notes newer than index mtime) and `--rebuild` (full scan). Index is append-only                                                                                                                                                                                                                                                                                                                         |
| `build-email-lookup.py`     | Called by `/w-daily` Phase 0                      | Extracts email→wikilink+VIP mapping from entity-registry.json. Self-skips if registry unchanged since last build                                                                                                                                                                                                                                                                                                                            |
| `check-plaud-completeness.py` | Called by `/w-daily` Phase 0.6                  | Compares Plaud API recordings for the target date against local files (`00-Inbox/` + `_attachments/`). Prints a warning listing missing recordings so the sync cursor can be lowered and re-pulled. Soft check, always exits 0, never blocks the run                                                                                                                                                                                          |
| `pull-plaud.py`             | Called by `/w-daily` Phase 0 (optional)           | Pulls new Plaud NotePin recordings via `plaud_api`, converts them to the structured transcript format in `00-Inbox/`. Incremental via `_db/plaud-sync.json`; exits 0 if no Plaud auth is configured. Resolves speakers via `_db/plaud-speaker-map.json` then `_db/email-lookup.json`. `--archive-ai` saves Plaud's own AI summary/minutes/outline to `_attachments/<stem>.plaud-ai.md`, written straight to `_attachments` so the note-writing agent never reads another model's synthesis                                                                                                                                    |
| `plaud_api.py`              | Imported by the Plaud scripts                     | Shared Plaud auth + API client. Resolves credentials OAuth-first (the `plaud login` token at `~/.plaud/tokens.json`), then legacy `PLAUD_TOKEN` from `_scripts/.env`. Normalizes both backends to one item shape, retries on transient errors                                                                                                                                                                                 |
| `enrich-plaud-transcripts.py` | Called by `/w-daily` Phase 0.5 (optional)       | Matches Plaud transcripts to calendar events by time overlap + subject similarity, rewrites their headers with calendar attendees/organizer                                                                                                                                                                                                                                                                                  |
| `archive-calendar.py`       | Called by `/w-daily` Phase 0                      | Persists today's calendar events to `_db/calendar-history.json` (7-day rolling window) for recording-to-meeting matching                                                                                                                                                                                                                                                                                                     |
| `process-capture.py`        | Called by `/w-daily`                              | Routes the daily note's `## Capture` section: `- [ ]` lines become tracked tasks in `07-Areas/My-Tasks.md`, plain lines move to `## Notes`                                                                                                                                                                                                                                                                                    |
| `build-person-index.py`     | Called by `/w-1on1` Phase 0                       | Scans `05-Interactions/**/*.md` frontmatter, builds `_db/person-index.json`: person→interactions map with summaries                                                                                                                                                                                                                                                                                                                        |
| `build-open-actions.py`     | Called by `/w-1on1`, `/w-review`; parser reused by `build-daily-briefings.py` | Extracts open items (`[ ]`, `[/]`, `[>]`, `[!]`) and completed (`[x]`, `[-]`) from interactions + projects + `07-Areas/06-Organization/` into `_db/open-actions.json`. Open items include `status` field (todo/in-progress/delegated/urgent). Indexed by owner/mentioned; completed as flat list. Excludes `[demoted::]` lines |
| `briefe.py`                 | Called by `/w-daily` Step 5                       | Rebuilds daily briefings from notes on disk (v2 single source of truth). For every touched date it ALWAYS rebuilds from ALL of that date's interaction/reference notes, regenerating the deterministic sections (meetings, emails, decisions, actions, ingestion count) via the `build-daily-briefings.py` render library, while PRESERVING the LLM-authored `## Attention needed` bullets and italic sign-off (from the existing note, or an `--overrides` file when provided). Rebuilding from disk means a rerun can never clobber a past day |
| `build-daily-briefings.py`  | Render library imported by `briefe.py`            | Deterministic daily-note render library (no longer a CLI): `build_briefing`, `merge_briefing_into_existing`, `build_new_daily_note`, imported by `briefe.py` and `rebuild-daily-from-notes.py`. Renders the generated block: meetings/emails/reference docs, decisions, and action items re-read from final note bodies with the open-actions parser. Key emails and reference docs cap at 5, decisions cap at 7, actions cap at 5 per group, overflow is surfaced. The `--inputs` envelope CLI path is pre-v2 and nothing produces its input files anymore |
| `finish.py`                 | Called by `/w-daily` Step 6                       | Git phase. Stale-lock age guard, allowlist-stages only the content trees `/w-daily` writes, commits only when something is staged, pushes only when there are unpushed commits. Push failure is categorized (`busy-deferred`, `publickey`, etc.), never fatal: data is safe on local disk and the next run retries. Always exits 0 |
| `rebuild-daily-from-notes.py` | Manual migration/helper                         | Rebuilds regular daily-note generated sections from final `05-Interactions/YYYY/` and `08-Reference/` notes. Preserves everything from `## Today's focus` onward. Used for historical compaction/backfill; weekly reviews are intentionally left untouched. |
| `audit-link-casing.py`      | On-demand (`--fix`)                               | Reports people-link wikilinks whose casing differs from the actual `04-People/` filename (filesystem = canonical truth); `--fix` normalizes them. Catches drift that resolves on case-insensitive Obsidian but breaks WSL scripts/indexes                                                                                                                                                                                                                                                                        |
| `prep-1on1-data.py`         | Called by `/w-1on1` Phase 0                       | Reads person-index + open-actions, extracts one person's data into compact JSON (~4KB) for agent context                                                                                                                                                                                                                                                                                                                                    |
| `audit-tasks.py`            | On-demand (`--dry-run` or `--fix`)                | Scans interaction notes for untagged action items. Auto-adds `[delegated-by::` for 1on1s and sent emails. Removes checkboxes from large meetings (>5 attendees). Flags small meetings for review                                                                                                                                                                                                                                            |
| `vault-health.py`           | On-demand / scheduled (cron)                      | Detect-only maintenance report. Original checks: overdue open actions, stale `status: stub` people (older than N days), archive-candidate interactions (older than 2 quarters with no open action), ghost ingest-log entries. Coherence checks (single content-tree pass, infra dirs excluded): broken wikilinks (a `[[target]]` that is neither a note stem nor a known `entity-registry.json` entry, case-insensitive so casing drift stays with `audit-link-casing.py`; attachment/`.base` embeds and punctuation-only links skipped), orphaned entity notes (`04-People`/`03-Projects` with no inbound links, ignoring notes younger than `--stale-stub-days`), and unresolved-entity notes (non-empty `unresolved-entities:` frontmatter). Writes `_db/maintenance-todo.md` (gitignored) plus a stderr summary; never fixes (detect-then-delegate). `--json` available |
| `backup-db.py`              | Called by `/w-daily` Phase 0                      | Snapshots critical `_db/` files (entity-registry, sanitize-mappings, email-lookup) to `_db/backups/YYYY-MM-DD/`. Rotation is bounded by age (`--keep-days` 7), count (`--keep-count` 14), and total size (`--max-total-mb` 200), and always keeps the most recent. Skips if today's backup exists |
| `utils.py`                  | Imported by all pipeline scripts                  | **Owner config single source** (the `OWNER CONFIG` block: `OWNER_SLUG`, `OWNER_NAME`, `OWNER_COMPANY`, `OWNER_PERSONAL_EMAILS`, `OWNER_WORK_EMAILS`, `OWNER_EMAILS`, `LOCAL_TZ`) plus shared utilities: `normalize_subject`, `subject_to_slug`, `guess_wikilink_from_email`, `company_from_domain`, `generate_pii_token`, `ensure_utf8_stdio`, `atomic_json_write`, `atomic_text_write`, `apply_vip_boost`, `recipient_set` |
| `pull-emails.log`           | Auto-generated                                    | Activity log for email/calendar pulling                                                                                                                                                                                                                                                                                                                                                                                                     |

---

## Obsidian Configuration

### Daily Notes Plugin (`.obsidian/daily-notes.json`)

- `folder: "00-Inbox"`: daily note button creates manual note in inbox
- `template: "_templates/daily-note"`: applies manual-note template

### Templater Plugin

- `trigger_on_file_creation: true`: auto-applies folder templates
- `enable_folder_templates: true`: see Templates table above for mappings

### Community Plugins

calendar, dataview, obsidian-tasks-plugin, omnisearch, quickadd, templater-obsidian, obsidian-icon-folder

---

## Transcript sources

The pipeline ingests meeting transcripts from any source that drops a file into `00-Inbox/`. A transcript is first-class when it has:

1. **A structured header** (`MeetingSubject:`, `MeetingDate:`, `Attendees:`, `MeetingType:`, `RecordingDuration:` in the first few lines): `classify-inbox.py` reads this directly and routes it to a note-writing agent (`prompts/transcript-note.md`) with pre-populated metadata, no re-detection needed.
2. **Timestamped speaker lines** (e.g. `[0:05:23] Sam: ...`): the `transcript-note.md` template resolves speakers per its own speaker-resolution section, which follows `.claude/rules/entity-matching.md` step 2 (`speakers_map` labels) and step 7 (participant cross-reference).

Bring your own recorder or transcription tool to produce that format. Generic transcripts (timestamps + speaker labels, no structured header) are also detected and processed. Plaud NotePin is supported out of the box (see below).

## Plaud NotePin

Secondary recording source. Plaud NotePin records meetings and provides cloud-based transcription with speaker diarization and AI summaries.

**Scripts:**
- `_scripts/plaud_api.py`: shared Plaud auth + API client (imported by pull-plaud.py and check-plaud-completeness.py). `load_plaud_auth()` resolves credentials **OAuth-first** (official `@plaud-ai/cli` token at `~/.plaud/tokens.json`, minted by `plaud login`, refreshed via a `plaud me` nudge; hits the developer API `platform.plaud.ai/developer/api`), then **legacy fallback** (`PLAUD_TOKEN` in `_scripts/.env`: the old web.plaud.ai `tokenstr` against `api-*.plaud.ai`; being retired). Both backends normalize to one item shape; transient 5xx/network errors retry
- `_scripts/pull-plaud.py`: pulls new recordings via `plaud_api`, converts to the structured transcript format, drops into `00-Inbox/`. Resolves speakers via the curated `_db/plaud-speaker-map.json` (checked first) then `_db/email-lookup.json` (handles Plaud's 32-char truncated emails with prefix matching). Surfaces unresolved / `Speaker N` labels in `_db/plaud-pull-summary.json`
- `_scripts/archive-calendar.py`: persists calendar events to `_db/calendar-history.json` (7-day rolling window, deduped)
- `_scripts/enrich-plaud-transcripts.py`: matches Plaud transcripts to calendar events by time overlap + subject similarity, rewrites headers with calendar attendees/organizer

**DB files:** `_db/plaud-sync.json` (last sync epoch), `_db/plaud-speaker-map.json` (curated Plaud-speaker→vault-person overrides), `_db/calendar-history.json` (persistent calendar)

**Pipeline integration:** Phase 0 pulls Plaud recordings and archives calendar. Phase 0.5 enriches Plaud transcripts with calendar data. Phase 1 classifies them as `transcript_mr` (the structured-transcript type). Cross-source dedup in classify-inbox.py handles dual-recording scenarios (prefers the local recording unless broken).

**Plaud transcript headers** (superset of the structured transcript format):
```
MeetingSubject: ...          # Plaud AI-generated title (kept even when calendar-matched)
MeetingDate: ...             # UTC timestamp from Plaud
Attendees: ...               # Calendar attendees (if matched), else resolved Plaud speakers (speaker-map -> email-lookup)
MeetingType: general
RecordingDuration: H:MM:SS
PlaudFileId: <hex>           # Plaud file ID for dedup + cross-reference
CalendarMatch: true/false    # Whether a calendar event was matched
CalendarSubject: ...         # Original calendar subject (if matched)
CalendarOrganizer: ...       # Calendar organizer email (if matched)
```

---

## Key Design Patterns

1. **Script-first preprocessing**: All deterministic work runs as Python/bash scripts: classification, header parsing, body cleaning, entity resolution, frontmatter generation, threading, batching, duplicate detection. LLM does content comprehension only. See `prepare.py`, `classify-inbox.py`, `finalize.py`
2. **Orchestration-only master**: `/w-daily` main context never reads email bodies or entity registry. It reads the compact manifest summary (~2KB), dispatches agents, and composes briefings from structured returns
3. **Manifest split**: `classify-inbox.py` writes full manifest to `_db/manifest.json` (all data for agents) and compact summary to stdout (counts, lists, batch plans for master). Eliminates large manifests in main context
4. **Staged-markdown agent returns**: Note-writing agents (transcript/email/doc) Write finished markdown notes (YAML frontmatter + body) into `_db/staged-notes/<output_filename>` and return a one-line `NOTE:` or `SKIP:`. No JSON envelopes: `finalize.py` picks the staged notes up, validates, applies hygiene, and moves them into place. The agent's frontmatter is authoritative and kept verbatim except a surgical summary patch
5. **Body bundling**: `classify-inbox.py` reads cleaned email bodies back after `--clean-bodies` pass, stores as `cleaned_body` on each manifest entry. Agents read bodies from manifest, not individual files
6. **Briefings rebuilt from disk**: `briefe.py` composes each day's briefing from ALL of that date's final notes on disk (meetings, emails, decisions, and actions via the open-actions parser), not from an ephemeral per-run snapshot. Only the LLM-authored `## Attention needed` and sign-off are carried across (via `--overrides`), so a rerun never loses bespoke content or clobbers a past day.
7. **Thread consolidation**: Emails in same thread → fewer, consolidated notes (HIGH gets full note + MEDIUM/LOW as bullets; MEDIUM-only thread → one note)
8. **Date-scoped briefings**: Backlog ingestion creates separate daily notes per email date, not processing date
9. **Stub creation threshold**: Direct interaction (≤5 recipients) → stub file. Mass CC (>5) → registry only
10. **Staging directory**: `00-Inbox/_processing/` during ingestion enables crash recovery
11. **VIP flagging**: People in entity registry can have a `"vip"` tier (boss-chain/stakeholder/team). Affects relevance scoring, adds `vip-involved:` frontmatter + `vip/` tags, and shows markers (`**!**`/`*`) in daily briefings. See `.claude/rules/vip.md`
12. **Inline vs agent dispatch**: In Step 2 the master applies the note templates inline for small batches (≤10 emails with no thread wider than 3, ≤3 docs); larger email backlogs and every transcript go to `general-purpose` note-writing agents. Typical morning: only transcript agents spawn
13. **Thread index**: `_db/thread-index.json` provides O(1) cross-batch thread lookup by ConversationId or normalized subject, replacing O(n) grep across interaction files
14. **Daily notes as scan layer**: Regular daily notes are generated blocks plus user-owned `## Today's focus` / `## Notes`. Generated sections are compact: emails/reference docs cap at 5, decisions cap at 7, actions cap at 5 Sam-owned + 5 waiting-on-others, with overflow pointers to source notes.
15. **Staged notes + crash safety**: Note-writing agents Write into `_db/staged-notes/`; `finalize.py` is the only mover into the vault. If `finalize.py` crashes (or a run dies between dispatch and finalize), the staged notes survive and Step 1's leftover check surfaces them on the next run, while the sources stay in `_processing/` for regeneration. A script-computed ETA (`classify-inbox.py`) prints a one-line heads-up when a run will be slow (transcript-heavy), without pausing. See `.claude/skills/w-daily/SKILL.md`.

---

## Naming Conventions

| Type           | Pattern                                     | Example                                  |
| -------------- | ------------------------------------------- | ---------------------------------------- |
| Email note     | `YYYY-MM-DD-email-{subject-slug}.md`        | `2026-03-05-email-q2-planning-status.md` |
| Meeting note   | `YYYY-MM-DD-{meeting-type}-{topic-slug}.md` | `2026-03-05-1on1-jordan-lee.md`          |
| Reference doc  | `YYYY-MM-DD-{original-stem}.md`             | `2026-03-05-product-roadmap.md`          |
| Daily note     | `YYYY-MM-DD.md`                             | `2026-03-05.md`                          |
| Weekly review  | `YYYY-WXX-weekly.md`                        | `2026-W10-weekly.md`                     |
| Monthly review | `YYYY-MM-monthly.md`                        | `2026-03-monthly.md`                     |
| Person file    | `FirstName-LastName.md`                     | `Sam-Rivera.md`                          |
| Team file      | `Group-Name.md` or `VS-Name.md`             | `Group-Legacy-Project.md`           |

---

## Model Routing

| Context                                                       | Model                 | Rationale                                |
| ------------------------------------------------------------- | --------------------- | ---------------------------------------- |
| Note-writing agents (transcript, email, doc)                  | Sonnet 4.6 (Haiku for low-stakes transcripts) | High volume, rule-driven, cost efficient |
| Synthesis agents (review, 1on1-prep)            | Opus 4.6 (explicit)   | Complex analysis, writing quality        |
| w-project-status, w-prep                                      | Opus 4.6 (explicit)   | Judgment-heavy, user-facing synthesis    |
| Master commands (w-setup, w-daily, w-review, w-1on1, w-task-audit) | Current session model | Orchestration only                       |

---

### Hooks

- **PostToolUse (Edit|Write)**: the only registered hook (`.claude/settings.json`). Warns when an edited/written file contains an HTML comment (`<!--`), which renders visibly in Obsidian.
- `session-init.sh`: **Removed** (2026-06-01). Never registered, and fully superseded: open tasks come from `My-Tasks.md`/`build-open-actions.py`, today's interactions + inbox count from the `/w-daily` briefing.
- `post-save.sh`: **Removed** (2026-03-09). Omnisearch plugin handles search indexing natively.

---

*This is a sanitized, generic copy of a working vault system. Project-specific development history has been removed. Update this date and note when you change skills, rules, templates, configs, or data flows.*

