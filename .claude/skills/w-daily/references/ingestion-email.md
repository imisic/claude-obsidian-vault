## Email Ingestion Rules

Email-specific rules extracted from the general ingestion pipeline. For general rules (content detection, routing, logging, file naming), see `ingestion.md`.

### Email source pulling (Windows scheduled task)

Email pulling is handled by `_scripts/Pull-Emails.ps1`, a PowerShell script that runs every 15 minutes via Windows Task Scheduler (task name: `Vault-PullEmails`).

**What it does:**
- Copies attachments from `EmailCapture/Vault/Attachments/` → `00-Inbox/_email-attachments/` (**first**, so an email pulled later in the same run always finds its attachments already staged)
- Copies `.txt` files from `EmailCapture/Sent/` → `00-Inbox/` with `SENT-` prefix
- Copies `.txt` files from `EmailCapture/Vault/` → `00-Inbox/` with original filename
- Moves originals to `Processed/` subfolder after successful copy
- Copies `*-calendar.json` from `EmailCapture/Calendar/` → `00-Inbox/` (overwrite, not move, calendar is a snapshot)
- Logs activity to `_scripts/pull-emails.log`

**Settle guard (received emails only):** a `.txt` whose `LastWriteTime` is under `$SettleSeconds` (120) old is skipped and retried next run. The capture flow writes the `.txt` *before* its attachment loop, so an email lands on disk about a second ahead of its own attachments. Pulling inside that window would process the email attachment-less and orphan the attachment permanently, since nothing re-links it afterwards. Deferring costs one scheduled cycle at most. Sent emails have no attachment capture and are not deferred.

**Wikilink-safe staging names:** attachment filenames are rewritten on copy (`[`→`(`, `]`→`)`, `|#^`→`-`). Outlook suffixes duplicate names with `[1]`, and `[ ] | # ^` all break Obsidian wikilink syntax. The flow's `yyyy-MM-dd_HHmmss-NN-` prefix contains none of these, so the rewrite never affects matching.

**Setup:** Run `_scripts/Install-EmailPullTask.ps1` once in elevated PowerShell.

**Source folders** (OneDrive, synced via Power Automate):
- **Sent**: `%USERPROFILE%\OneDrive - Acme Corp\EmailCapture\Sent\`
- **Received**: `%USERPROFILE%\OneDrive - Acme Corp\EmailCapture\Vault\`
- **Attachments**: `%USERPROFILE%\OneDrive - Acme Corp\EmailCapture\Vault\Attachments\`

### Email attachments

The capture flow saves each attachment to `EmailCapture/Vault/Attachments/` named `<yyyy-MM-dd_HHmmss>-NN-<original name>`, where the timestamp is `formatDateTime(receivedDateTime,'yyyy-MM-dd_HHmmss')` and `NN` is a per-email counter (set the flow's `Apply to each` to Degree of Parallelism 1 so the counter cannot race).

**The receive-second is the only join key.** There is no `Attachments:` header, and the filename carries no thread or message id. `ConversationId` is deliberately NOT usable: it identifies the *thread*, so every reply shares it, and keying on it would make the third reply's `deck.pdf` overwrite the first's.

Correlation (`classify-inbox.py`):
- `attachment_stamp(date_str)` derives the prefix from the `Date:` header by **string slicing**, not datetime parsing. Power Automate's `formatDateTime` does not shift the UTC offset, so normalising to local time here would invent a mismatch.
- `find_staged_attachments(stamp, dir)` prefix-matches via `iterdir()`, never `glob()`: an Outlook `[1]` suffix is a glob character class and would silently match nothing.
- Staged files matching no email are reported to stderr as `Warning: staged attachment matches no email:`. Only a matching email moves them out, so they would otherwise accumulate unseen.

`finalize.py:move_email_attachments()` moves matched files to `_attachments/email/<stamp>/`, keyed on the stamp rather than the email's `.txt` stem (subject slugs run long and can carry emoji). The raw file is always kept and linked from the email note's `attachments:` frontmatter (see originals policy in `ingestion.md`).

**Substantive attachments are also promoted to reference notes.** During `--resolve-entities`, classify-inbox.py scans each note-producing email's correlated attachments and, for document types (`utils.is_promotable_attachment`: `.pdf/.doc/.docx/.ppt/.pptx/.xls/.xlsx`; images, `.ics/.vcf/.p7s`, archives, and other non-content files are excluded by omission), appends a `docs` manifest entry so the ordinary doc pipeline converts the file to markdown in `08-Reference/`. Each such entry carries `is_email_attachment: true`, `source_email` (the email note stem, emitted as a `source-email:` backlink in the reference note), a stamp-stripped `clean_stem` for the reference filename, and the email's own `date`. The raw file is NOT duplicated: `finalize.py` writes the reference note but skips source-deletion for `is_email_attachment` docs, so the parent email's `move_email_attachments` still relocates the single raw file and the email keeps its `attachments:` link. Net result: content searchable in `08-Reference/`, raw evidence linked from the email, one physical copy. Only note-producing (HIGH/MEDIUM) emails promote, since a promoted attachment relies on its email's move to relocate the raw file. Manually dropping an attachment into `00-Inbox/` still works as before for anything the filter skips.

**Not covered:** sent-mail attachments (no `EmailCapture/Sent/Attachments` folder exists).

**Key conventions:**
- The `SENT-` filename prefix is the primary signal for `direction: sent` detection during parsing
- EmailCapture folders are a queue: only unprocessed emails sit there
- `/w-daily` does NOT pull emails. It only processes what's already in `00-Inbox/`

### Email preprocessing
Apply the full pipeline from `references/email-preprocessing.md`:
1. Clean body (strip Teams footers, disclaimers, signatures, safe links)
2. Detect duplicates (skip if duplicate found)
3. Score relevance (HIGH / MEDIUM / LOW)
4. Extract thread context from quoted replies
5. Identify email threads (group by normalized subject)

**Tiered routing:**
- **HIGH**: Full interaction note with cleaned body, actions, thread context
- **MEDIUM**: Condensed note: frontmatter + 1-line summary + thread context, no full body
- **LOW**: No note created. Log to `_db/ingest-log.json` with `"action": "skipped-low-relevance"`, `"subject"`, `"date"`, `"to"`, `"summary": "one-line description"`

### Power Automate email format
Emails captured by Power Automate arrive as `.txt` files in TWO different formats:

**Received emails** (no `SENT-` filename prefix) use `Type ` prefix on header keys:
```
Type Category: Uncategorized
Type From:  sender@example.com
Type To:  recipient1@example.com;recipient2@example.com
Type CC:
Type Subject:  RE: Some Topic
Type Date:  2026-03-06T14:30:00+00:00
Type ConversationId:  AAQkAGI2...

Plain text body...
```

**Sent emails** (filename starts with `SENT-`) use plain header keys, and `From:` is always empty:
```
From:
To: recipient@example.com
CC: optional@example.com
Subject: Re: Some Topic
Date: 2026-03-02T08:37:27+00:00
ConversationId: AAQkAGI2...

Plain text body...
```

**Detection:** A `.txt` file is a Power Automate email if its first 8 lines contain (`From:` or `Type From:`) AND (`Subject:` or `Type Subject:`) AND (`Date:` or `Type Date:`). `ConversationId:` / `Type ConversationId:` is optional. Older emails may not have it.

**Two formats** (distinguished by `SENT-` filename prefix after pull):
- **Received** (no prefix): headers prefixed with `Type ` (e.g., `Type From:`)
- **Sent** (`SENT-` prefix): plain headers, empty `From:` field

**Parsing rules:**
1. Read lines sequentially until first blank line, these are headers
2. Headers use `Key: Value` format. For received emails, strip the `Type ` prefix first (e.g., `Type From:  value` → key=`From`, value=`value`). Note: values may have leading whitespace after the colon, trim it
3. Multiple recipients in `To:`/`CC:` are separated by semicolons (`;`), not commas
4. Parse `Date:` as ISO datetime → `date` frontmatter (extract YYYY-MM-DD)
5. If `Category:` exists and isn't "Uncategorized", add `email-category: value`
6. If `ConversationId:` exists, store as `conversation-id:` in frontmatter (raw string, no transformation)
7. `From:`/`To:`/`CC:` go through entity matching (see `.claude/rules/entity-matching.md`)
8. Everything after the first blank line following headers is the body

**Sent email detection (direction: sent):**
Determine `direction: sent` using this priority:
1. If filename starts with `SENT-` → sent (always, regardless of From field)
2. If `From:` matches Sam's email addresses → sent
3. If `From:` is empty → sent (Power Automate sent emails always have empty From)
4. Otherwise → received (omit direction field)

The `SENT-` prefix is added by `Pull-Emails.ps1` during the automated pull from OneDrive EmailCapture.

### Frontmatter for emails
```yaml
date: YYYY-MM-DD
type: email
interaction-type: email
from: "[[FirstName-LastName]]"
to:
  - "[[FirstName-LastName]]"
cc:                        # optional, only if CC exists
  - "[[FirstName-LastName]]"
subject: the email subject
summary:                   # 1-line plain-text summary, max 120 chars, no wikilinks or markdown
email-category:            # optional, from Power Automate Category (omit if Uncategorized)
conversation-id:           # optional, from Power Automate ConversationId (Outlook thread ID)
direction:                 # sent (if filename starts with SENT- or From matches Sam), omit for received
relevance: high            # high, medium, or low
thread-context:            # optional, 1-line summary of replied-to content
email-thread:              # optional, links to related emails in same thread
  - "[[2026-03-06-email-related-subject]]"
project:                   # link if identifiable from content
vip-involved:              # optional, list of VIP tiers present among participants (see vip.md)
  - boss-chain
tags:                      # optional, VIP tags for Obsidian filtering
  - vip/boss-chain
email-thread-count:        # optional, set when note consolidates N emails from same thread
status: unprocessed        # only for HIGH relevance, omit for MEDIUM
source-file: original-filename.txt
attachments:               # optional, pre-built by classify-inbox from the staged files
  - "[[email/<stamp>/<filename>]]"
```
