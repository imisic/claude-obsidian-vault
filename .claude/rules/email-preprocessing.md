## Email Preprocessing Rules

Applied to ALL emails (sent and received) before ingestion routing.

### Step 1: Clean email body

Strip the following from email body text (in order):

1. **Teams/Webex meeting footers**: remove everything from the first occurrence of any of these markers to the end of that block. English only by default; if your org sends non-English mail, add your locale's equivalents.
   - `________________________________________________________________________________` followed by Teams meeting content
   - `Microsoft Teams meeting`
   - `Join: https://teams.microsoft.com/meet/`
   - `Meeting ID:`
   - Lines containing `webex.com/msteams`
   - `Join on a video conferencing device`
   - `For organizers: Meeting options`
   - `Need help?`
   - `Org help`
   - `IMPORTANT - DATA AND INFORMATION PROTECTION`
   - `Company Logo [https://static.acme.example/`
   - `Privacy and security`
   - Lines with only `Passcode:` content

2. **Email disclaimers**: remove blocks matching (English only by default; add your org's localized disclaimer opening line).
   - English corporate disclaimer starting with `DISCLAIMER:The contents of this email`
   - Acme Digital disclaimer starting with `IMPORTANT - DATA AND INFORMATION PROTECTION`

3. **Safe links**: Simplify Outlook safe links:
   - Replace `[https://eur05.safelinks.protection.outlook.com/?url=https%3A%2F%2F...]` with the decoded original URL
   - Or simply remove the safe link wrapper leaving just the display text

4. **Email signatures**: Remove repeated signature blocks:
   - `ACME CORP` + `Acme Corp - Internal Division` + `Service & Engagement` + phone number
   - `company-logo.png [cid:...]`
   - Any `[cid:...]` image references (embedded images that don't exist on disk)

5. **Whitespace cleanup**:
   - Collapse 3+ consecutive blank lines to 2
   - Trim trailing whitespace on lines
   - Remove lines that are only `*****` or only dashes `---...`

### Step 1.5: Sanitize PII in body

After cleaning, replace PII in email body text with tokens before AI agents see it. Implemented in `classify-inbox.py --sanitize-pii`.

- **Email addresses** in body text: replaced with `[EMAIL-xxxx]` tokens (4-char alphanumeric)
- **Phone numbers** in body text (international format `+NNNNNNNNNNNN`): replaced with `[PHONE-xxxx]` tokens
- **Headers (From/To/CC) are NOT sanitized**, needed for entity resolution
- Token mappings stored in `_db/sanitize-mappings.json` (bidirectional: PII-to-token and token-to-PII)
- New PII auto-generates a new token; known PII reuses existing tokens
- Transcripts are not sanitized (emails only)

### Step 2: Detect duplicates

Before creating a note, check if another email note already exists with:
- Same subject (normalized: lowercase, stripped of Re:/Fw:/Fwd: prefixes, plus any locale prefixes you added)
- Same date (same calendar day, not same timestamp)
- Same recipient set (To+CC as a set, order doesn't matter, both must match as sets)
If duplicate found: skip and log to `_db/ingest-log.json` as `"skipped-duplicate"`

### Step 3: Score relevance

Score each email using this **waterfall** (check in order, first match wins):

1. If ANY LOW pattern matches AND no HIGH signal is present → **LOW**
2. If ANY HIGH signal is present → **HIGH** (HIGH always overrides LOW)
3. If ANY MEDIUM pattern matches → **MEDIUM**
4. Default (no signals match) → **MEDIUM**

Signals per tier:

**HIGH: Full ingestion as interaction note:**
- Sam's original text is > 5 lines (not counting quoted replies)
- Contains data, numbers, percentages, metrics
- Contains decision language: "aligned", "agreed", "decided", "approved", "proposal"
- Contains pushback language: "pushing back", "flagging", "concern", "not aligned", "disagree"
- Contains delegation: "please [verb]", "can you [verb]", "action needed", "@[name]"
- Contains escalation: "urgent", "blocker", "risk", "gap"
- Contains career/comp/HR topics: "raise", "bonus", "promotion", "compensation", "transition"
- Subject contains "PRD", "OKR", "planning", "status", "health check"
- Multiple recipients (> 3 in To+CC) AND substantive body

**MEDIUM: Condensed note (frontmatter + 1-line summary + thread context, no full body):**
- Short reply (3-5 lines) to a substantive thread (keep the context from quoted reply)
- Delegation to specific person without detailed instructions
- Acknowledgment with added info ("Yes, and also...")
- Forwarding with 3+ lines of original context or commentary

**LOW: Log only (no note created, just entry in ingest-log):**
- Meeting invite with only template text ("placeholder for our regular 1-on-1s")
- Meeting reschedule/update with no substantive content
- Body is < 3 lines AND is purely logistical ("running late", "can't make it", "thanks")
- Forwards with ≤2 lines of Sam's original text (e.g., "FYI", "Looping in", "See below", "Worth checking")
- Duplicate send (same body sent to same/similar recipients within 1 hour)
- Personal/admin emails (invoices, IT requests, HRnet access), detect by:
  - To: contains `@acme.example` AND subject in your org's local language (not the work lingua franca) AND no work-project context
  - To: `Service.Desk@acme.example`
- Pure acknowledgments: body is only "Thanks", "Awesome", "Will do", "OK", "+1"
- Photo/attachment-only follow-ups ("forgot to attach", "here's the file")

### Step 3.5: VIP relevance adjustment

Now handled by `classify-inbox.py` using `apply_vip_boost()` from `_scripts/utils.py`. See `.claude/rules/vip.md` for tier definitions and boost rules. The boost is applied during `--resolve-entities` and stored in the pre-generated frontmatter. Agents and inline processing do NOT re-apply it.

**Known limitation**: Duplicate detection (Step 2) runs before VIP boost (Step 3.5). If an email was scored LOW on a previous run (VIP not yet in registry) and the same email reappears after a registry update, the ingest-log dedup check will skip it. The VIP boost only applies prospectively. See vip.md "Timing note".

### Step 4: Extract thread context from quoted replies

For MEDIUM and HIGH emails, quoted replies contain valuable context. Extract:
1. Find the original message in the quote chain (first `From:` block, or your locale's equivalent header)
2. Extract a 1-3 sentence summary of what was being replied to
3. Include this as `thread-context:` in the frontmatter
4. For the note body, include Sam's reply + a collapsed "Thread context" section with the most relevant quoted message (not all quotes in a deep chain)

### Step 5: Identify email threads

**Primary method, ConversationId (preferred):**
When emails have a `ConversationId` header (added to Power Automate flows 2026-03-07), group by ConversationId. All emails sharing the same ConversationId are definitively in the same thread, regardless of subject line changes.

**Fallback method, subject normalization:**
When ConversationId is absent (older emails captured before the flow update), fall back to grouping by normalized subject (strip Re:/Fw:/Fwd: prefixes, case-insensitive).

**Mixed scenarios:** If some emails in a batch have ConversationId and others do not, use ConversationId as the primary grouping key. Then merge in any ConversationId-less emails whose normalized subject matches emails already grouped by ConversationId.

When multiple emails share a thread, apply thread consolidation (Step 6).

### Step 6: Thread consolidation

When a thread contains multiple emails from the same batch, consolidate rather than creating separate thin notes:

**Thread with at least one HIGH email:**
- Create the full note for the HIGH email (as normal)
- MEDIUM emails in the same thread: do NOT create separate notes. Instead, add them as chronological bullets in the HIGH note's body under a `### Thread activity` section
- Each bullet: `- **YYYY-MM-DD HH:MM** [[Sender]]: one-line summary`
- LOW emails: log-only, but mention in the HIGH note's thread context

**Thread with only MEDIUM emails (no HIGH):**
- Create ONE consolidated thread note for the most recent email
- Name it after the thread topic (not a specific reply): `YYYY-MM-DD-email-{thread-topic-slug}.md`
- Use the most recent date as the note date
- Include a chronological summary of all MEDIUM emails in the thread as bullets
- Frontmatter `relevance: medium`, set `email-thread-count: N` to indicate consolidation

**Thread with only LOW emails:**
- Log all to ingest-log, no notes created

**Standalone emails (not part of any thread):**
- Process normally per their relevance tier (HIGH → full note, MEDIUM → condensed note, LOW → log only)

**Cross-batch threads (emails from previous batches):**
- If an existing note in 05-Interactions/ matches the thread (same ConversationId or normalized subject):
  - HIGH in current batch + existing note (any relevance): create new HIGH note, cross-link via `email-thread:` in both directions
  - MEDIUM in current batch + existing HIGH note: append to existing note's `### Thread activity` section
  - MEDIUM in current batch + existing MEDIUM note: append chronological bullet to existing note
  - LOW in current batch + existing HIGH/MEDIUM note: log as normal (no note), but add a reference in the existing note's `email-thread:` field if the LOW email adds context (e.g., forwarded to new people)
  - LOW in current batch + no existing note: log only, no note created

### Frontmatter additions (all emails)

These fields are part of the standard email frontmatter (see `ingestion.md`):
```yaml
direction: sent            # only for sent emails, omit for received
relevance: high|medium|low
thread-context: "Brief summary of what this is replying to"
email-thread:              # if part of a multi-email thread
  - "[[2026-03-06-email-engineering-team-devices]]"
```
