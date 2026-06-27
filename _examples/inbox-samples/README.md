# Inbox samples (example raw inputs)

> [!example] Everything in this folder is fictional demo data (persona Sam Rivera @ Acme Corp). These are **raw inputs**, the kind of file you drop into `00-Inbox/`, shown so you can see what the scripts and processors consume *before* they become notes. They live here, not in `00-Inbox/`, so a fresh clone's first `/w-daily` stays a clean no-op.

The rest of the vault already ships with the **outputs** these inputs would produce (daily notes, interaction notes, reference notes, reviews, all marked with an `[!example]` callout). This folder shows the other half: the inputs.

## What's here

| Sample file | What it represents | Becomes |
|---|---|---|
| `received-email-orion-pilot-checklist.txt` | A received email captured by Power Automate (note the `Type ` header prefix and empty `From` handling rules) | A **HIGH**-relevance email note in `05-Interactions/2026/` |
| `SENT-reply-orion-pilot-checklist.txt` | A sent email (the `SENT-` filename prefix is the signal for `direction: sent`; `From:` is empty) | Folded into the same thread (consolidation) |
| `2026-03-06-transcript-orion-pilot-readiness.txt` | A structured meeting transcript (header block + timestamped speaker lines) | A `meeting-type: sync` note in `05-Interactions/2026/` |
| `2026-03-06-transcript-orion-pilot-readiness.json` | The transcript's companion JSON (canonical metadata source the recorder produces; classify-inbox prefers it over the `.txt` header) | (read alongside the `.txt`) |
| `manual-note-2026-03-06.md` | A daily manual note from Obsidian (`type: manual-note`); its `## Capture` tasks route to `07-Areas/My-Tasks.md` | Merged into `01-Daily/2026/2026-03-06.md` |

These samples are a self-contained mini-scenario dated **2026-03-06**, one day after the seeded examples, so processing them creates *new* notes instead of colliding with the ones already in the vault.

## Try it

1. Copy the files into `00-Inbox/` (the manual note and the email/transcript files; leave this README behind):

   ```bash
   cp _examples/inbox-samples/received-email-orion-pilot-checklist.txt \
      _examples/inbox-samples/SENT-reply-orion-pilot-checklist.txt \
      _examples/inbox-samples/2026-03-06-transcript-orion-pilot-readiness.txt \
      _examples/inbox-samples/2026-03-06-transcript-orion-pilot-readiness.json \
      _examples/inbox-samples/manual-note-2026-03-06.md \
      00-Inbox/
   ```

2. In Claude Code, run `/w-daily 2026-03-06`.
3. Watch it classify, resolve people to wikilinks, write interaction notes, route the capture tasks, and build the `2026-03-06` daily briefing.

To reset, delete the notes it created (under `05-Interactions/2026/`, `01-Daily/2026/2026-03-06.md`, and the new lines in `07-Areas/My-Tasks.md`) and the originals are already removed from `00-Inbox/` by the pipeline (email/manual-note originals are deleted; the transcript moves to `_attachments/`).
