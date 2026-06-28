# Vault System Specification

Single reference document describing how the entire system works. **Keep this updated** when changing skills, rules, templates, configs, or data flows.

---

## Architecture Overview

This is an Obsidian-based personal knowledge management system for a knowledge worker who runs projects and meetings. It automates email ingestion, document processing, meeting management, and periodic reviews through a modular skill-based architecture running on Claude Code. A product-management preset (products, markets, segments, OKRs, steering-committee meetings) is optional and toggled during `/w-setup`. Throughout this document the owner is referred to by the example persona "Sam Rivera" at the fictional "Acme Corp". Replace these with your own details when you adopt the vault (run `/w-setup`, or see the repository README's Configuration section). The repository ships with a seeded, cross-linked **example dataset** built around this persona so each skill's output is visible out of the box; every seeded file carries an `[!example]` Obsidian callout, and raw input samples live in `_examples/inbox-samples/`. See the README's "Example content" section to explore or clear it.

**Core principles:**

- Skills orchestrate and execute, Rules define standards
- `/w-daily` is the single entry point for all ingestion
- Processor agents run on Sonnet (volume), synthesis agents on Opus (quality)
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
    → Phase 0: Bootstrap registry if missing, refresh indexes (incremental: fast no-op if nothing changed).
                thread-index: only scans new notes; email-lookup: skips if registry unchanged; ingest-log audit: weekly only
    → Phase 1: Run classify-inbox.py --clean-bodies --sanitize-pii --resolve-entities --thread-index (scripted).
                Classifies, parses headers, cleans email bodies, sanitizes PII (emails/phones → tokens),
                resolves all entities to wikilinks+VIP, pre-generates frontmatter+filenames, groups threads,
                detects duplicates, plans batches.
                Full manifest → _db/manifest.json (~50KB). Compact summary → stdout (~2KB).
                Master reads summary into context; reads manifest file only for targeted agent prompts.
    → Phase 1.5.1: Run create-stubs.py (scripted). Creates person stubs from unresolved entities,
                updates entity-registry, email-lookup, and sanitize-mappings. Also resurrects any
                previously-archived people who reappear in the inbox: moves their file from
                04-People/_archived/ back to 04-People/, clears status=archived in the registry,
                and logs a RESURRECT row in people-archive-analysis.csv.
    → Phase 1.6: Handle definitive LOWs (pre-scored by script), skipped transcripts ([Recovered]),
                 empty meeting preps: log + delete sources without LLM work.
    → Phase 1.7: Print an ETA heads-up when the run will be slow (estimate from classify-inbox.py).
                 Lite mode: ask which transcripts to synthesize now vs defer.
    → Phase 1.5a: If ≤6 emails + no complex threads, process emails inline (decoupled from transcripts/docs).
                  Reads cleaned_body from manifest, builds structured JSON, calls write-notes.py.
    → Phase 1.5b: If ≤3 docs, convert inline via markitdown (skip doc agent)
    → Phase 1.9: If >20 emails (backlog), process batches sequentially with progress reporting
    → Phase 2: Invoke processor agents in parallel (Sonnet), only for content NOT handled inline
                ├── email-processor (only if >6 emails or complex threads) → returns structured JSON
                ├── doc-processor (only if >3 docs) → writes files directly
                └── transcript-processor (always dispatched as agent; in lite mode, only the
                    transcripts you chose, the rest become deferred stub notes) → returns structured JSON
    → Phase 2.2: For each agent return, call write-notes.py to write notes + update ingest-log
    → Phase 3: Post-process (create people stubs, update registry, validate notes)
    → Phase 4: Create daily notes per date, clean/condense/merge manual notes (never raw copy-paste)
    → Phase 5: Generate daily briefings from briefing_data[] + final note bodies
                (actions are read from final written notes after task hygiene)
    → Phase 6: Report to user
```

**Outputs:** Interaction notes in `05-Interactions/YYYY/`, reference docs in `08-Reference/`, person stubs in `04-People/`, daily notes in `01-Daily/YYYY/`

**Run modes:** `/w-daily` (full, default, unchanged), `/w-daily lite` (emails + docs full, transcripts deferred to thin `status: deferred` stub notes that you choose per run; raw transcript parked in `_attachments/`), and `/w-daily --upgrade-deferred` (synthesize deferred stubs in place via Phase U, then rebuild affected daily briefings). The slow-run ETA prints on full and lite runs alike, only when `classify-inbox.py` estimates more than ~2 minutes. See `.claude/skills/w-daily/SKILL.md` Phases 1.7, 2.0, and U.

---

## Skills

### User-Invocable (7)

| Skill            | Command                                       | What it does                          | Creates                               |
| ---------------- | --------------------------------------------- | ------------------------------------- | ------------------------------------- |
| w-setup          | `/w-setup`                                    | Setup wizard: interview → configure vault | utils.py owner block, registry, notes |
| w-daily          | `/w-daily [YYYY-MM-DD] [lite] [--upgrade-deferred]` | Master ingestion + daily note builder (lite defers transcripts; upgrade synthesizes deferred ones later) | Daily notes, interaction notes, stubs |
| w-review         | `/w-review weekly\|monthly\|last N days\|...` | Period review with vault analysis     | Weekly/monthly review notes           |
| w-1on1           | `/w-1on1 [Person Name]`                       | 1on1 meeting prep                     | Pre-populated meeting note            |
| w-project-status | `/w-project-status [Name]`                    | Project/product status summary (Opus) | Inline report (no file)               |
| w-prep           | `/w-prep [person/topic] [last wk]`            | Conversation prep (fwd) or "what I did" recap (retro), cross-repo (Opus) | Inline brief; optional 00-Inbox prep note |
| w-task-audit     | `/w-task-audit [--fix]`                       | Action item hygiene audit             | Inline report, optional fixes         |

### Internal Processor Agents (Sonnet)

| Agent                | Model             | Tools     | Input                                                         | Output                                                                         |
| -------------------- | ----------------- | --------- | ------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| email-processor      | Sonnet 4.6 (fork) | Read only | Manifest (cleaned bodies, resolved entities, frontmatter)     | Structured JSON: `{ notes[], log_entries[], briefing_data[], new_entities[] }` |
| doc-processor        | Sonnet 4.6 (fork) | All       | Batch of document files                                       | Writes reference notes directly, returns plain-text summary                    |
| transcript-processor | Sonnet 4.6 (fork) | Read only | Manifest (resolved attendees, frontmatter) + transcript files | Structured JSON: `{ notes[], log_entries[], briefing_data[], new_entities[] }` |

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
| `ingestion.md`            | Content type detection, routing, meeting/reference frontmatter, file naming, originals policy, manual note processing, logging                                    | w-daily, all processors                        |
| `ingestion-email.md`      | Email pulling, Power Automate format, email parsing rules, email frontmatter schema, tiered routing                                                               | email-processor, w-daily                       |
| `email-preprocessing.md`  | Body cleaning (Teams footers, disclaimers, safe links), duplicate detection, relevance scoring (HIGH/MEDIUM/LOW waterfall), thread identification + consolidation | email-processor                                |
| `entity-matching.md`      | Name/email → wikilink resolution, registry schema, domain → company mapping, Sam detection, stub creation threshold, recipient parsing                           | All processors                                 |
| `vip.md`                  | VIP tier definitions (boss-chain/stakeholder/team), relevance boost rules, frontmatter tags, briefing markers                                                     | email-processor, transcript-processor, w-daily |
| `obsidian-conventions.md` | Vault structure, frontmatter formats, action item rules (single source of truth), linking conventions, periodic note formats, archive policy                      | All skills                                     |
| `verification.md`         | Anti-fabrication, no-false-absence, and verify-don't-trust rules for entity matching, extraction, and synthesis. Inlined into the forked processor skills (which do not read rules at runtime); referenced by the synthesis skills | All processors, review-agent, 1on1-prep, w-project-status |

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

Lightweight email→wikilink+VIP lookup extracted from entity-registry.json (~10KB vs the full registry). Used by inline processing (Phase 1.5) and email-processor (Phase D) for fast entity resolution. **Self-skips rebuild** if registry mtime < lookup mtime (no work if registry hasn't changed since last build).

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
| `Pull-Emails.ps1`           | Windows Task Scheduler, every 15 min              | Copies emails from OneDrive EmailCapture → `00-Inbox/`, adds `SENT-` prefix to sent. Also copies calendar JSON from `EmailCapture/Calendar/`                                                                                                                                                                                                                                                                                                |
| `Install-EmailPullTask.ps1` | One-time manual (elevated PS)                     | Registers the scheduled task                                                                                                                                                                                                                                                                                                                                                                                                                |
| `check-environment.py`      | Called by `/w-setup`; runnable anytime            | Doctor: reports optional tools (markitdown, defuddle, Plaud CLI) AND vault integrity (`_db/` present, `entity-registry.json` and `ingest-log.json` parse), each with a fix hint. Stdlib-only; `--json` for the skill, `--strict` exits 1 on a failed required check. Nothing in optional tools is required to start: PDF/HTML/images/text process with zero installs                                                                                                                                                                                                              |
| `apply-setup.py`            | Called by `/w-setup` Step 3                       | Deterministic writer for setup answers (`_db/setup-answers.json`): rewrites the marker-bounded `OWNER CONFIG` block in `utils.py`, builds `entity-registry.json` (owner + manager + VIPs + projects), repoints `bookmarks.json`, copies `.env` from example. Idempotent; prose edits are left to the `/w-setup` skill                                                                                                                          |
| `classify-inbox.py`         | Called by `/w-daily` Phase 1                      | Full deterministic preprocessing: classification, header parsing, body cleaning (`--clean-bodies`), PII sanitization (`--sanitize-pii`), entity resolution (`--resolve-entities`), frontmatter+filename generation, thread grouping, duplicate detection, batch planning. Pre-scores definitive LOWs, filters recovered transcripts, checks meeting prep content. Bundles `cleaned_body` (sanitized) into manifest. Computes an `eta` block (full/lite run minutes + `slow` flag + per-item breakdown) and stamps each transcript with its `stakes` (substantive/low-stakes) for the lite-mode heads-up and choice prompt. Outputs full manifest to `_db/manifest.json` + compact summary to stdout |
| `create-stubs.py`           | Called by `/w-daily` Phase 1.5.1                  | Reads manifest unresolved_entities, creates person stub files in `04-People/`, updates entity-registry.json, email-lookup.json, and sanitize-mappings.json. Applies stub threshold (≤5 recipients = file, >5 = registry-only). **Resurrects archived people**: if an inbound email matches a registry entry with `status=archived`, moves the file from `04-People/_archived/` back to `04-People/`, clears the status flag, and appends a `RESURRECT` row to `_db/people-archive-analysis.csv`. **Case-collision guard**: skips any unresolved entity whose filename collides case-insensitively with an existing person file (never appends a registry entry or writes the stub), surfacing it as `skipped_case_collision[]`. Prevents the case-insensitive-FS clobber where a transcript attendee slug like `Sam-Rivera` downcased to `sam-rivera` would overwrite the real `Sam-Rivera.md` |
| `write-notes.py`            | Called by `/w-daily` Phase 1.5a, 2.2              | Central I/O handler for agent returns: takes structured JSON (from agents or inline), serializes YAML frontmatter, **applies `apply_task_hygiene()` per body line (stamps `[created::]`, auto-converts non-Sam tasks in large group settings to plain bullets, auto-adds `[delegated-by:: [[Sam-Rivera]]]` in 1on1s / small meetings / sent emails; VIP protection is per-task owner, not whole-note)**, writes notes with collision avoidance (`-2`, `-3` suffix; per-note `overwrite: true` bypasses it for in-place `/w-daily --upgrade-deferred` upgrades), deletes source files (or moves to `_attachments/` when `move_to_attachments: true`, which also writes lite-mode deferred-transcript stubs and parks their raw files), processes `skipped_log_entries` (log + delete source, OR move to `_attachments/` when the entry sets `move_to_attachments: true`, used for duplicate transcripts captured by a second device), updates ingest-log with dedup guard. Returns JSON `{ written[], deleted[], moved_to_attachments[], skipped_deleted[], logged, errors[] }` |
| `validate-notes.py`         | Called by `/w-daily` Phase 3.4                    | Type-aware validation: interaction notes require interaction fields; references require `date/type/source-file`; projects/workstreams/people use lighter schemas. Also lints interaction-note bodies for raw `@mentions`, leaked Dataview, and un-tokenized PII (email/phone in email bodies). Returns JSON pass/fail per note and is safe for vault-wide audits without treating people/projects like interactions. |
| `check-ingest-log.sh`       | Called by `/w-daily` Phase 0                      | Removes ghost entries + 90-day rotation. Supports `--if-stale` to run weekly only (checks `_db/.last-audit`)                                                                                                                                                                                                                                                                                                                                |
| `_check_ingest_log_impl.py` | Called by `check-ingest-log.sh`                   | Python helper for the ingest-log audit: deduplicates entries by `source-file` (prefers `created` over `skipped`) and removes ghost `created` entries whose `output-file` no longer exists                                                                                                                                                                                                                                                   |
| `build-thread-index.py`     | Called by `/w-daily` Phase 0                      | Supports `--incremental` (only scan notes newer than index mtime) and `--rebuild` (full scan). Index is append-only                                                                                                                                                                                                                                                                                                                         |
| `update-thread-index.py`    | Called by `/w-daily` Step 3.1b                    | Appends new email note entries to `_db/thread-index.json` after note creation. Reads frontmatter, extracts conversation-id/subject, normalizes, deduplicates by path. Usage: `--notes path1.md path2.md` or `--stdin`                                                                                                                                                                                                                       |
| `build-email-lookup.py`     | Called by `/w-daily` Phase 0                      | Extracts email→wikilink+VIP mapping from entity-registry.json. Self-skips if registry unchanged since last build                                                                                                                                                                                                                                                                                                                            |
| `check-plaud-completeness.py` | Called by `/w-daily` Phase 0.6                  | Compares Plaud API recordings for the target date against local files (`00-Inbox/` + `_attachments/`). Prints a warning listing missing recordings so the sync cursor can be lowered and re-pulled. Soft check, always exits 0, never blocks the run                                                                                                                                                                                          |
| `pull-plaud.py`             | Called by `/w-daily` Phase 0 (optional)           | Pulls new Plaud NotePin recordings via `plaud_api`, converts them to the structured transcript format in `00-Inbox/`. Incremental via `_db/plaud-sync.json`; exits 0 if no Plaud auth is configured. Resolves speakers via `_db/plaud-speaker-map.json` then `_db/email-lookup.json`                                                                                                                                    |
| `plaud_api.py`              | Imported by the Plaud scripts                     | Shared Plaud auth + API client. Resolves credentials OAuth-first (the `plaud login` token at `~/.plaud/tokens.json`), then legacy `PLAUD_TOKEN` from `_scripts/.env`. Normalizes both backends to one item shape, retries on transient errors                                                                                                                                                                                 |
| `enrich-plaud-transcripts.py` | Called by `/w-daily` Phase 0.5 (optional)       | Matches Plaud transcripts to calendar events by time overlap + subject similarity, rewrites their headers with calendar attendees/organizer                                                                                                                                                                                                                                                                                  |
| `archive-calendar.py`       | Called by `/w-daily` Phase 0                      | Persists today's calendar events to `_db/calendar-history.json` (7-day rolling window) for recording-to-meeting matching                                                                                                                                                                                                                                                                                                     |
| `process-capture.py`        | Called by `/w-daily`                              | Routes the daily note's `## Capture` section: `- [ ]` lines become tracked tasks in `07-Areas/My-Tasks.md`, plain lines move to `## Notes`                                                                                                                                                                                                                                                                                    |
| `build-person-index.py`     | Called by `/w-1on1` Phase 0                       | Scans `05-Interactions/**/*.md` frontmatter, builds `_db/person-index.json`: person→interactions map with summaries                                                                                                                                                                                                                                                                                                                        |
| `build-open-actions.py`     | Called by `/w-1on1`, `/w-review`; parser reused by `build-daily-briefings.py` | Extracts open items (`[ ]`, `[/]`, `[>]`, `[!]`) and completed (`[x]`, `[-]`) from interactions + projects + `07-Areas/06-Organization/` into `_db/open-actions.json`. Open items include `status` field (todo/in-progress/delegated/urgent). Indexed by owner/mentioned; completed as flat list. Excludes `[demoted::]` lines |
| `build-daily-briefings.py`  | Called by `/w-daily` Phase 5                      | Deterministically renders the generated block in daily notes. Meetings/emails/reference docs come from `briefing_data[]`; action items are re-read from final note bodies with the open-actions parser. Key emails and reference docs cap at 5, decisions cap at 7, actions cap at 5 per group, overflow is surfaced. Flags lite-mode deferred transcripts `(deferred, not yet synthesized)` from a `briefing_data.deferred` flag. |
| `rebuild-daily-from-notes.py` | Manual migration/helper                         | Rebuilds regular daily-note generated sections from final `05-Interactions/YYYY/` and `08-Reference/` notes. Preserves everything from `## Today's focus` onward. Used for historical compaction/backfill; weekly reviews are intentionally left untouched. |
| `audit-link-casing.py`      | On-demand (`--fix`)                               | Reports people-link wikilinks whose casing differs from the actual `04-People/` filename (filesystem = canonical truth); `--fix` normalizes them. Catches drift that resolves on case-insensitive Obsidian but breaks WSL scripts/indexes                                                                                                                                                                                                                                                                        |
| `prep-1on1-data.py`         | Called by `/w-1on1` Phase 0                       | Reads person-index + open-actions, extracts one person's data into compact JSON (~4KB) for agent context                                                                                                                                                                                                                                                                                                                                    |
| `audit-tasks.py`            | On-demand (`--dry-run` or `--fix`)                | Scans interaction notes for untagged action items. Auto-adds `[delegated-by::` for 1on1s and sent emails. Removes checkboxes from large meetings (>5 attendees). Flags small meetings for review                                                                                                                                                                                                                                            |
| `vault-health.py`           | On-demand / scheduled (cron)                      | Detect-only maintenance report: overdue open actions, stale `status: stub` people (older than N days), archive-candidate interactions (older than 2 quarters with no open action), and ghost ingest-log entries. Writes `_db/maintenance-todo.md` (gitignored) plus a stdout summary; never fixes (detect-then-delegate). `--json` available |
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

1. **A structured header** (`MeetingSubject:`, `MeetingDate:`, `Attendees:`, `MeetingType:`, `RecordingDuration:` in the first few lines): `classify-inbox.py` reads this directly and routes to `transcript-processor` with pre-populated metadata, no re-detection needed.
2. **Timestamped speaker lines** (e.g. `[0:05:23] Sam: ...`): the transcript-processor resolves speakers per `speaker-resolution.md`.

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

1. **Script-first preprocessing**: All deterministic work runs as Python/bash scripts: classification, header parsing, body cleaning, entity resolution, frontmatter generation, threading, batching, duplicate detection. LLM does content comprehension only. See `classify-inbox.py`, `write-notes.py`, `check-ingest-log.sh`
2. **Orchestration-only master**: `/w-daily` main context never reads email bodies or entity registry. It reads the compact manifest summary (~2KB), dispatches agents, and composes briefings from structured returns
3. **Manifest split**: `classify-inbox.py` writes full manifest to `_db/manifest.json` (all data for agents) and compact summary to stdout (counts, lists, batch plans for master). Eliminates large manifests in main context
4. **Structured agent returns**: Email-processor and transcript-processor return structured JSON (`notes[]`, `log_entries[]`, `briefing_data[]`, `new_entities[]`) instead of writing files. `write-notes.py` handles all I/O. Doc-processor still writes files directly
5. **Body bundling**: `classify-inbox.py` reads cleaned email bodies back after `--clean-bodies` pass, stores as `cleaned_body` on each manifest entry. Agents read bodies from manifest, not individual files
6. **Briefing-ready returns**: Processor agents return per-note `briefing_data` with summaries, VIP flags, actions, decisions. Daily composition uses summaries/decisions from `briefing_data`, but actions are re-read from final written note bodies so task hygiene is authoritative.
7. **Thread consolidation**: Emails in same thread → fewer, consolidated notes (HIGH gets full note + MEDIUM/LOW as bullets; MEDIUM-only thread → one note)
8. **Date-scoped briefings**: Backlog ingestion creates separate daily notes per email date, not processing date
9. **Stub creation threshold**: Direct interaction (≤5 recipients) → stub file. Mass CC (>5) → registry only
10. **Staging directory**: `00-Inbox/_processing/` during ingestion enables crash recovery
11. **VIP flagging**: People in entity registry can have a `"vip"` tier (boss-chain/stakeholder/team). Affects relevance scoring, adds `vip-involved:` frontmatter + `vip/` tags, and shows markers (`**!**`/`*`) in daily briefings. See `.claude/rules/vip.md`
12. **Inline processing (decoupled)**: Phase 1.5a processes ≤6 emails inline regardless of whether transcripts/docs exist. Phase 1.5b processes ≤3 docs inline via markitdown. Transcript-processor always runs as an agent. Typical morning: only transcript agent spawns
13. **Thread index**: `_db/thread-index.json` provides O(1) cross-batch thread lookup by ConversationId or normalized subject, replacing O(n) grep across interaction files
14. **Daily notes as scan layer**: Regular daily notes are generated blocks plus user-owned `## Today's focus` / `## Notes`. Generated sections are compact: emails/reference docs cap at 5, decisions cap at 7, actions cap at 5 Sam-owned + 5 waiting-on-others, with overflow pointers to source notes.
15. **Lite mode + deferred transcripts**: `/w-daily lite` processes emails/docs fully but defers transcripts (the wall-clock sink) to thin `status: deferred` stub notes, chosen interactively per run; the raw transcript moves to `_attachments/`. `/w-daily --upgrade-deferred` later synthesizes each stub in place (`write-notes.py` `overwrite`), then rebuilds affected daily briefings. A script-computed ETA (`classify-inbox.py`) warns up front, but only when a run will be slow (>~2 min). See `.claude/skills/w-daily/SKILL.md` Phases 1.7, 2.0, U.

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
| Processor agents (email, doc, transcript)                     | Sonnet 4.6            | High volume, rule-driven, cost efficient |
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

