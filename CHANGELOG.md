# Changelog

Notable changes to this template. Versions follow [semantic versioning](https://semver.org/) and are tagged in git.

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
