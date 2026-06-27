# Screenshots and runtime duplicate handling

Load this when the manifest entry has `screenshots[]` populated OR when you detect at processing time that two transcripts cover the same meeting.

## Screenshots

Structured transcripts may include slide screenshots captured during the meeting. The manifest's `transcripts[i].screenshots[]` array lists each PNG with `path` (read this file with the Read tool, Sonnet is multimodal), `basename` (final filename, use this in wikilinks), `timestamp_seconds`, and `timestamp_str` (HH:MM:SS).

For each screenshot:

1. **Read the PNG** via the Read tool. Content renders visually.
2. **Caption it** in 1 line (max ~120 chars). Language: match the dominant language of nearby transcript segments at that timestamp. Caption should describe what's on the slide (deck title + 1-2 key data points), not "this is screenshot 5 from the meeting."
3. **Embed inline** in the body under the matching `## Discussion` bullet. Find the bullet whose discussion topic best fits the screenshot's `timestamp_str` (closest preceding moment). Embed format:

```
- Discussion bullet text...
  ![[<basename>]]
  *<caption>*
```

Indented 2 spaces under the bullet. Wikilink uses the **basename only**. `write-notes.py` will rewrite the path during attachment move.

4. **Track basenames**: the note's `screenshot_files` field must list the original `path` for each embedded screenshot. `write-notes.py` reads this to move PNGs to `_attachments/screenshots/<stem>/` and rewrite wikilinks to the final path.

If a screenshot's timestamp falls outside any Discussion bullet (e.g., a slide shown without verbal context), append it to a trailing `## Screenshots` section instead, with `[HH:MM:SS]` anchors.

Generic transcripts (no transcript companion) have no `screenshots[]`. Skip this entire step.

## Runtime-detected duplicates

(`classify-inbox.py` deduplicates upstream. Same-meeting Plaud/MR pairs should not reach you. This applies only when the upstream dedup misses, typically because timestamps or durations are unusual.)

When you discover at processing time that two transcripts cover the same meeting (different subjects, same content):

1. Pick the primary (usually the structured transcript; richer metadata + speaker labels).
2. Add the duplicate to `skipped_log_entries[]`, NOT `log_entries[]`. The two arrays are not interchangeable: only `skipped_log_entries[]` triggers move-to-attachments in `write-notes.py`.
3. Set on that entry:
   - `source-file`: the duplicate's filename (e.g., `transcript-plaud-2026-05-18-weekly-meeting.txt`), NEVER `null`
   - `action: "skipped-duplicate"`
   - `move_to_attachments: true`
   - `summary`: one line explaining which transcript won and why
4. Do NOT move the file yourself. `write-notes.py` reads `source-file` and moves it from `00-Inbox/_processing/` to `_attachments/`.

Failure mode: putting a runtime-detected duplicate in `log_entries[]` with `source-file: null` (the common slip) leaves the source file stuck in `_processing/` after the run finishes.

## Duplicate / already-processed check

Before processing each file:

1. Check `_db/ingest-log.json` for a matching `source-file`.
2. If found with `action: "created"` → verify `output-file` exists on disk. If missing → process normally (ghost entry).
3. If found with `action: "skipped-*"` → add to `skipped_log_entries` in return.
4. If not found → process normally.
