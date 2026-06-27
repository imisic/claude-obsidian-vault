## Ingestion Rules

These rules define HOW ingestion works. The `/w-daily` command orchestrates WHEN.
- Entity matching: `.claude/rules/entity-matching.md`
- Email-specific rules (pulling, Power Automate format, parsing, email frontmatter): `.claude/rules/ingestion-email.md`
- Email preprocessing (cleaning, relevance scoring, threading): `.claude/rules/email-preprocessing.md`

### Conversion tools
Each format has a zero-install fallback where possible (see `doc-processor` for the per-file logic):
- **PDF**: `markitdown` if installed, else read it directly with the Read tool (PDFs are readable natively)
- **DOCX/PPTX/XLSX**: `markitdown` required (binary Office formats); if absent, skip and report
- **HTML**: `defuddle` if installed, else read the file directly and extract the main content
- **Images (PNG/JPG)**: read directly (multimodal), no tool needed
- **.eml/.msg**: `markitdown` (extract headers manually if it fails)
- **.txt (Power Automate emails)**: parse directly, no conversion needed
- **MD/TXT (non-email)**: copy content as-is

### Content type detection
Determine type BEFORE routing. Check in this order:

**Skip** (do not process):
- `*-calendar.json` files: consumed by the recorder app, not by ingestion pipeline. Leave in place.
- `_processing/` subdirectory contents: staging area for in-flight files

**Email** (route to 05-Interactions/YYYY/):
- `.txt` file matching Power Automate format (`From:`/`Type From:` AND `Subject:`/`Type Subject:` AND `Date:`/`Type Date:` in first 8 lines)
- `.eml` or `.msg` file extension

**Structured transcript** (route to 05-Interactions/YYYY/ as meeting):
- `.txt` file with `MeetingSubject:` AND `MeetingDate:` AND `Attendees:` in first 6 lines
- Filename contains `transcript-`
- Has structured header block + timestamped speaker lines in various formats:
  - `[H:MM:SS] Speaker N:` (gap-based heuristic fallback)
  - `[H:MM:SS] Sam:` (config user name)
  - `[H:MM:SS] FirstName-LastName:` (voice profile with mapped display name)
  - `[H:MM:SS] voice-NNN:` (voice profile, not yet mapped to a person)
  - `[H:MM:SS] SPEAKER_NN:` (raw pyannote diarization label)
  - `[H:MM:SS] Unknown:` (no diarization match)
- Metadata (subject, date, attendees, meeting-type) is pre-populated in header. Use directly
- Companion `.json` and `.md` files share the same stem (e.g. `<stem>.txt` + `<stem>.json` + `<stem>.md`). The `.json` is the canonical source: `classify-inbox.py` reads it for richer metadata (float-precision screenshot timestamps, speaker→profile map, quality flags) when present and prefers its values over the `.txt` header. The `.md` is a pre-built preview, unused by the pipeline.
- Screenshots live in `00-Inbox/_screenshots/` with filename pattern `<sessionId>-screenshot-NN.png`. The `.json`'s `annotations.screenshots[]` array lists each with float-second `timestamp` and basename `path`. The transcript-processor embeds them inline under the matching Discussion bullet with a 1-line caption.

**Generic transcript** (route to 05-Interactions/YYYY/ as meeting):
- Has timestamps like `[00:15:23]` or `(00:15)`
- Has speaker labels like `Sam:`, `Jordan:`, `Speaker 1:`
- Continuous dialogue format

**Manual note** (route to 01-Daily/YYYY/):
- `.md` file with `type: manual-note` in frontmatter
- Created by Obsidian daily note template in `00-Inbox/`

**Manual meeting note** (route to 05-Interactions/YYYY/):
- `.md` file with `type: meeting` AND `interaction-type: meeting` in frontmatter
- Created by Obsidian meeting templates (general, 1on1, sync, steerco) in `00-Inbox/`
- Already has correct frontmatter structure from template. Needs content cleaning only

**Meeting prep note** (subtype of manual meeting note):
- `.md` file with `type: meeting` AND `meeting-prep: true` in frontmatter
- Created by `/w-1on1` prep agent
- Contains two zones: Prep (disposable) and Meeting (carried forward)
- See "Meeting prep note processing" section below for merge logic

**Meeting notes** (route to 05-Interactions/YYYY/):
- External meeting notes without proper frontmatter
- Contains "Attendees:", "Agenda:", "Minutes:", "Action items:"
- Has attendee lists (bullet lists of names near the top)

**Reference doc** (route to 08-Reference/):
- Everything that doesn't match the above

### Routing and frontmatter
- Manual notes → merge into `01-Daily/YYYY/YYYY-MM-DD.md` (see manual note processing below)
- Manual meeting notes → clean/condense, then move to `05-Interactions/YYYY/` (see manual meeting note processing below)
- Emails → `05-Interactions/YYYY/` with type: email, interaction-type: email
- Meetings/transcripts → `05-Interactions/YYYY/` with type: meeting, interaction-type: meeting
- Reference docs → `08-Reference/` with type: reference
- Always add frontmatter: date, source-file, type
- Only HIGH relevance notes get `status: unprocessed` (for manual review). MEDIUM and meetings: omit status field

### Daily note structure

When creating a new daily note in `01-Daily/YYYY/YYYY-MM-DD.md`, use this structure:

```yaml
date: YYYY-MM-DD
type: daily
week: N
```

```markdown
# DayOfWeek, Month DD

(briefing goes here, generated by Phase 5)

## Today's focus
1.
2.
3.

## Notes

```

**Merging into existing daily notes**: If the daily note already exists (e.g., backlog processing adds emails for a past date):
- **Briefing sections** (between H1 heading and `## Today's focus`): Append new content to existing sections. If a section already has content, add the new items below existing items (do not duplicate).
- **Today's focus / Notes**: Never overwrite, only append.
- If the existing note has no briefing yet, insert it normally.

### Frontmatter for reference docs
```yaml
date: YYYY-MM-DD
type: reference
source-file: original-filename
summary:                   # optional, 1-line description of the document
project:                   # optional, link if related to a project
tags: []                   # optional
```

Note: Reference docs do NOT get `status: unprocessed`. That field is email-specific.

### Manual note processing
When a manual note (`.md` with `type: manual-note` in frontmatter) is found in inbox:
1. Read its `date:` frontmatter to determine which daily note it belongs to
2. Extract content from `## Today's focus` and `## Notes` sections
3. **Clean and condense** the content before merging (never raw copy-paste). Target 25-50% of original length, keep substance, cut filler:
   - Fix grammar, typos, and rushed writing
   - Condense to key points, remove filler, keep substance
   - Structure with proper headings and bullets
   - Resolve all names to `[[wikilinks]]` via entity registry
   - Convert `@Name` mentions to `[[Name]]` wikilinks
   - Extract implicit action items / follow-ups
   - Preserve all references to people, products, projects, decisions, and specific data points
4. In the target daily note (`01-Daily/YYYY/YYYY-MM-DD.md`):
   - If daily note doesn't exist → create it with standard daily note structure
   - If `## Today's focus` has items → append to (not replace) the daily note's `## Today's focus` section
   - If `## Notes` has content → append to the daily note under `## Notes` section (create if missing)
5. Delete the manual note from inbox after merging
6. Log to ingest-log with `type: "manual-note"`, `action: "merged"`

### Manual meeting note processing
When a manual meeting note (`.md` with `type: meeting` in frontmatter, WITHOUT `meeting-prep: true`) is found in inbox:
1. Read its frontmatter. It already has `date`, `type`, `interaction-type`, `meeting-type` from the template
2. **Clean and condense** the body content (never raw copy-paste). Target 25-50% of original length, keep decisions and rationale, cut filler:
   - Fix grammar, typos, and rushed writing
   - Condense discussion points to key substance, remove filler, keep decisions and rationale
   - Structure with clear headings and bullets
   - Resolve all names to `[[wikilinks]]` via entity registry (convert `@Name` → `[[Name]]`)
   - Populate `attendees:` frontmatter from names found in `## Attendees` section or body
   - Populate `project:` frontmatter if identifiable from content
   - Add `summary:` frontmatter: 1-line plain-text summary (no wikilinks)
   - Ensure action items use proper format: `- [ ] [[Owner]] description [due:: YYYY-MM-DD]`
   - Only keep Sam-relevant actions (same rules as email action extraction)
3. Remove template scaffolding that wasn't filled in (empty placeholder sections, the Actions checkpoint callout)
4. Remove all Dataview queries from the note: both fenced code blocks (` ```dataview `) and inline queries (`` `= this.file.name` ``, `` `$= dv.pages(...)` ``). They were useful during the meeting in Obsidian but don't belong in the processed note.
5. Move the cleaned note to `05-Interactions/YYYY/` (using the date from frontmatter for YYYY)
6. Reference the meeting in the daily note briefing (same as any other meeting, add to Meetings section, extract decisions and actions)
7. Delete the original from inbox
8. Log to ingest-log with `type: "meeting"`, `action: "created"`

### Meeting prep note processing
When a meeting prep note (`.md` with `meeting-prep: true` in frontmatter) is found in inbox:

The prep note has two zones:
- **Prep zone**: Everything between `## Prep (auto-generated` and the next `---` divider. This is ephemeral context, ALWAYS discarded during processing.
- **Meeting zone**: `## Discussion`, `## Actions`, `## Next time` sections. This is user-written content, carried forward if non-empty.

**Scenario A: Transcript exists for the same meeting**
Match by: same `date:` AND (same `person:` for 1on1s, OR same `meeting-type` + overlapping attendees for other meetings).

1. Let the transcript-processor create the primary meeting note as usual
2. Check if the prep note's Meeting zone has content (Discussion/Actions/Next time, beyond the empty template placeholders `-` and `- [ ]`)
3. If Meeting zone has content:
   - Clean and condense the user's notes (same rules as manual meeting processing)
   - Append to the transcript note under a `## Manual notes` section
   - Merge any action items into the transcript note's `## Actions`
4. If Meeting zone is empty: nothing to merge, transcript note stands alone
5. Delete the prep note from inbox
6. Log: `type: "meeting-prep"`, `action: "merged-with-transcript"`

**Scenario B: No transcript for this meeting**
1. Discard the Prep zone entirely (everything between `## Prep (auto-generated` and `---`)
2. Remove Dataview queries
3. Check if Meeting zone has content beyond empty placeholders
4. If Meeting zone has content: process as a normal manual meeting note (clean, condense, add summary, move to `05-Interactions/YYYY/`)
5. If Meeting zone is empty: discard the entire note, there's nothing to save. Log: `type: "meeting-prep"`, `action: "skipped-empty"`
6. Delete the prep note from inbox

### Frontmatter for meetings
```yaml
date: YYYY-MM-DD
type: meeting
interaction-type: meeting
meeting-type: general      # REQUIRED, one of: general, 1on1, steerco, sync
person:                    # 1on1 only, wikilink to the other person, e.g. "[[FirstName-LastName]]"
summary:                   # 1-line plain-text summary, max 120 chars, no wikilinks or markdown
attendees:
  - "[[FirstName-LastName]]"
project:                   # link if identifiable
vip-involved:              # optional, list of VIP tiers present among attendees (see vip.md)
  - boss-chain
tags:                      # optional, VIP tags for Obsidian filtering
  - vip/boss-chain
source-file: original-filename
```

### Frontmatter for structured transcripts
```yaml
date: YYYY-MM-DD
type: meeting
interaction-type: meeting
meeting-type: 1on1             # REQUIRED, from MeetingType header (1on1, steerco, sync, general)
summary:                       # 1-line plain-text summary, max 120 chars, no wikilinks or markdown
attendees:
  - "[[FirstName-LastName]]"   # from Attendees header, resolved via entity matching
project:                       # link if identifiable from subject/content
vip-involved:                  # optional, list of VIP tiers present among attendees (see vip.md)
  - boss-chain
tags:                          # optional, VIP tags for Obsidian filtering
  - vip/boss-chain
recording-duration: "HH:MM:SS" # from RecordingDuration header
source-file: original-filename.txt
```

Note: Structured transcript headers (`MeetingSubject`, `MeetingDate`, `Attendees`, `MeetingType`) provide pre-classified metadata. The transcript-processor should use these directly instead of re-detecting from content.

### Originals policy
| Content type | After processing | Rationale |
|-------------|-----------------|-----------|
| Manual notes (.md from Obsidian) | **Delete from 00-Inbox/** | Content merged into daily note in 01-Daily/ |
| Manual meeting notes (.md from Obsidian) | **Delete from 00-Inbox/** | Cleaned note moved to 05-Interactions/YYYY/ |
| Documents (PDF/DOCX/PPTX/XLSX/HTML) | **Delete from 00-Inbox/** | Fully converted to .md in 08-Reference/ |
| Emails (.txt from inbox) | **Delete from 00-Inbox/** | Content captured in interaction note. OneDrive originals in `Processed/` subfolder |
| Structured transcripts (.txt) | **Move to _attachments/** | Verbatim transcript has value beyond structured note |
| Transcript companions (.json, .md) | **Move to _attachments/** alongside the .txt | Same stem; the .json carries the canonical metadata/segments/quality data, the .md is a generated preview |
| Transcript screenshots (.png) | **Move to `_attachments/screenshots/<transcript-stem>/`** | Embedded inline in the meeting note via wikilinks; rewritten by write-notes.py to the final path |
| Generic transcripts | **Move to _attachments/** | Verbatim transcript has value beyond structured note |
| Calendar JSON (.json) | **Leave in 00-Inbox/** | Overwritten by next Pull-Emails.ps1 run; consumed by the recorder app |

### File naming
- Email: `YYYY-MM-DD-email-{subject-slug}.md` (slug: lowercase, hyphens, max 50 chars)
- Meeting: `YYYY-MM-DD-{meeting-type}-{topic-slug}.md`
- Reference: `YYYY-MM-DD-{original-stem}.md`

**Collision handling**: If a file with the same name already exists in the target directory, append `-2`, `-3`, etc. before the `.md` extension (e.g., `2026-03-09-email-q2-planning-2.md`). Never silently overwrite.

### Logging
Log ALL actions to `_db/ingest-log.json`. Each entry:
```json
{
  "timestamp": "ISO datetime",
  "source-file": "original-filename.txt",
  "action": "created|skipped-low-relevance|skipped-duplicate|merged|merged-with-transcript|skipped-empty|failed",
  "output-file": "path or null",
  "type": "email|meeting|reference",
  "subject": "for emails",
  "date": "YYYY-MM-DD",
  "summary": "1-line description"
}
```

### Ingest-log integrity
**NEVER trust the ingest-log alone to determine if a file was already processed.** When checking for duplicates or prior processing:
1. Check `_db/ingest-log.json` for a matching `source-file` entry
2. If the entry has `action: "created"`, **verify the `output-file` exists on disk**
3. If the output file does NOT exist → treat as unprocessed (ghost entry from a failed run)
4. If the entry has `action: "skipped-*"` → trust it (no output file expected)

On startup, the master command (`/w-daily`) runs a log integrity check: remove any `action: "created"` entries whose `output-file` does not exist on disk.

### Action item extraction
- Look for patterns: "TODO", "action:", "follow up", "will do", "@name will", "please [verb]"
- Format emitted by agents: `- [ ] [[Owner-Name]] description [due:: YYYY-MM-DD] [source:: [[note-name]]]`
- `[created:: YYYY-MM-DD]` is stamped by `write-notes.py` at write time. Agents do NOT emit it.
- `write-notes.py` applies the task hygiene matrix automatically (VIP-aware, size-based).
- Place in `## Actions` section at the bottom of the note

### Registry maintenance
- New people discovered during ingestion → stub file created by master command (not by agents)
- New emails learned → update registry entry
- New projects → add to registry
