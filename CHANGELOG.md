# Changelog

Notable changes to this template. Versions follow [semantic versioning](https://semver.org/) and are tagged in git.

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
