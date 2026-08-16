## Ingestion

`/w-daily` is the single entry point for all ingestion. There is no separate ingest command.

The procedures themselves are long, so they load on demand instead of every session. **Before processing anything out of `00-Inbox/`, read the one you need:**

- `.claude/skills/w-daily/references/ingestion.md` - how ingestion works: conversion tools per format, content detection, routing, logging, file naming, note frontmatter.
- `.claude/skills/w-daily/references/ingestion-email.md` - email pulling (`_scripts/Pull-Emails.ps1`), Power Automate `.txt` format, parsing, email frontmatter schema, VIP handling, attachment correlation.
- `.claude/skills/w-daily/references/email-preprocessing.md` - the pipeline applied to every email before routing: body cleaning, dedupe, relevance scoring, thread context.

Never infer a routing, naming or frontmatter decision from memory. These rules encode format quirks (Teams footer markers, `SENT-` prefixes, attachment ordering) that are not guessable. If you are touching inbox content and have not read the relevant file above, read it first.
