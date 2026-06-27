## Email Ingestion Rules

Email-specific rules extracted from the general ingestion pipeline. For general rules (content detection, routing, logging, file naming), see `ingestion.md`.

### Email source pulling (Windows scheduled task)

Email pulling is handled by `_scripts/Pull-Emails.ps1`, a PowerShell script that runs every 15 minutes via Windows Task Scheduler (task name: `Vault-PullEmails`).

**What it does:**
- Copies `.txt` files from `EmailCapture/Sent/` → `00-Inbox/` with `SENT-` prefix
- Copies `.txt` files from `EmailCapture/Vault/` → `00-Inbox/` with original filename
- Moves originals to `Processed/` subfolder after successful copy
- Copies `*-calendar.json` from `EmailCapture/Calendar/` → `00-Inbox/` (overwrite, not move, calendar is a snapshot)
- Logs activity to `_scripts/pull-emails.log`

**Setup:** Run `_scripts/Install-EmailPullTask.ps1` once in elevated PowerShell.

**Source folders** (OneDrive, synced via Power Automate):
- **Sent**: `%USERPROFILE%\OneDrive - Acme Corp\EmailCapture\Sent\`
- **Received**: `%USERPROFILE%\OneDrive - Acme Corp\EmailCapture\Vault\`

**Key conventions:**
- The `SENT-` filename prefix is the primary signal for `direction: sent` detection during parsing
- EmailCapture folders are a queue: only unprocessed emails sit there
- `/w-daily` does NOT pull emails. It only processes what's already in `00-Inbox/`

### Email preprocessing
Apply the full pipeline from `.claude/rules/email-preprocessing.md`:
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
```
