# Changelog

Notable changes to this template. Versions follow [semantic versioning](https://semver.org/) and are tagged in git.

## v2.2.0 (2026-08-16)

Correctness fixes, a lighter always-on context, and a test suite worth the name.

### Changed
- The three ingestion procedures (`ingestion.md`, `ingestion-email.md`, `email-preprocessing.md`) moved out of `.claude/rules/` into `.claude/skills/w-daily/references/`, where they load on demand. Everything in `rules/` loads into every session, and those three were procedures rather than rules: a script-driven `/w-daily` run reads none of them, because `classify-inbox.py` and `finalize.py` already encode the decisions. `.claude/rules/ingestion.md` is now an 11-line pointer naming the three files and when to reach for one. Always-on rules drop from 846 lines to 263.
- `briefe.py --touched` accepts both `--touched D1 D2` and `--touched D1 --touched D2`. The space-separated form, which `w-daily/SKILL.md` itself shows, previously aborted the whole briefing step on an argparse error.
- `vault-health.py` gains `--orphan-grace-days` (default 14). `--stale-stub-days` used to drive both the stub report and the orphan grace window, so raising one silently loosened the other; it now covers stubs only, and defaults to 90.
- Effort pins on `review-agent`, `w-review`, and `w-task-audit` drop from `xhigh` to `high`.
- The test suite grows from 60 to 140 tests, adding cover for task hygiene (the VIP/size/forgettability matrix), the open-actions index, the briefing empty-input guard, ingest-log dedup, and capture routing.

### Fixed
- `finalize.py` repoints a note's own `[source:: [[...]]]` references when the file is renamed. Agents cite the manifest-assigned filename while correcting `meeting-type`, so the rename used to strand every action's backlink, or worse, leave it resolving to a real but different note. Anchored to the final path including any `-2` collision suffix.
- `vault-health.py` no longer counts a `[[target]]` inside a code fence or code span as a wikilink. Documentation about wikilinks was inventing broken links and inflating inbound counts, which can mask a genuinely orphaned note.
- `vault-health.py` no longer reports people under `04-People/_archived/` as orphans. That folder is where a dormant person is deliberately put, and `create-stubs.py` resurrects them on reappearance, so flagging them re-reported the fixed state forever.
- Stub staleness is measured from a `created:` field that `create-stubs.py` now stamps, instead of file mtime. Any touch (a company backfill, a bulk relink) used to reset the clock, so routine maintenance silently emptied the report.
- Five citations pointed at things that do not exist: a CLAUDE.md "Vault prose voice" section referenced by `1on1-prep` and `review-agent`, a "Model selection" note in `w-daily/SKILL.md` referenced by `w-1on1` and `w-review`, and `speaker-resolution.md` in SYSTEM.md, deleted with the fork processor skills in v2.0.0.
- `obsidian-conventions.md` said action checkboxes live only in `05-Interactions/` and `03-Projects/`, but `build-open-actions.py` also indexes the `07-Areas/06-Organization/` hub pages. A checkbox on a Partner or Product page is an indexed task truth, not an accidental second one.

## v2.1.0 (2026-07-23)

Email attachments reach the vault, and their content becomes searchable.

### Added
- Email attachments are pulled from `EmailCapture/Vault/Attachments/`, staged in `00-Inbox/_email-attachments/`, and matched to their email by receive-second. Matched files move to `_attachments/email/<stamp>/` and the email note links them via a new `attachments:` frontmatter list. Previously nothing read that folder, so attachments never reached the vault at all.
- Document-type attachments (PDF and Office) on note-producing emails are promoted to `08-Reference/` notes through the existing doc pipeline, cross-linked back to the email via `source-email:`. The raw file is not duplicated: content is searchable, raw evidence stays linked, one physical copy on disk.
- `pull-plaud.py --archive-ai` saves Plaud's own AI summary, minutes, and outline to `_attachments/<transcript-stem>.plaud-ai.md`. It is written straight to `_attachments`, bypassing the inbox, so the note-writing agent never reads another model's synthesis. Replaces `--include-ai` (**breaking** if you scripted that flag), which appended the same content to the transcript the agent reads.

### Fixed
- Transcript attendees: a recorder writes the vault owner as a bare name token with no `@`, which fell through to the email guesser and produced a miscased, unresolved wikilink plus a person stub that had to be blocked on every run. It now short-circuits to the configured owner slug.
- 1on1 auto-detection compared attendees against a hardcoded owner wikilink instead of `OWNER_SLUG`, so it silently stopped working for anyone who configured a different owner.
- `/w-task-audit` rebuilds the open-actions index even on a dry run, and reports the builder's `total_open` field. It previously could report a stale count, and its wording invited a `len()` over a dict that returns the key count rather than the task count.

## v2.0.0 (2026-07-06)

Pipeline rebuild: scripts orchestrate, models only produce content.

### Changed
- `/w-daily` ingestion is rebuilt around one-shot note-writing agents. Each agent reads one source and Writes one finished markdown note into `_db/staged-notes/`; a deterministic post-pass (`finalize.py`) does all validation, task hygiene, moves, logging, and indexing. Models no longer emit JSON envelopes, which removes an entire class of parsing bugs.
- Daily briefings always rebuild from the notes on disk (`briefe.py`), so a rerun or a backdated note can never clobber a past day's briefing.
- New pipeline scripts: `prepare.py` (single pre-dispatch entry point), `finalize.py`, `briefe.py`, `finish.py`. `build-daily-briefings.py` is now the render library the briefing builder imports.
- Note-writing prompts live in `.claude/skills/w-daily/prompts/{transcript,email,doc}-note.md`.
- `w-daily/SKILL.md` shrank from roughly 780 to about 105 lines. A light morning runs materially faster.

### Removed
- The `context: fork` processor skills (`email-processor`, `doc-processor`, `transcript-processor`), replaced by the prompt templates above.
- `write-notes.py`, `validate-notes.py`, and `update-thread-index.py`, folded into `finalize.py`.
- The lite / transcript-deferral machinery, including the `lite` and `--upgrade-deferred` arguments to `/w-daily` (**breaking**).

## v1.0.0

Initial sanitized, shareable template release.
