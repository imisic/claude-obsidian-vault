## Obsidian Conventions

### Core linking
- All notes use YAML frontmatter
- Dates: ISO format YYYY-MM-DD everywhere
- Product links: [[ProductName]], one file per product in 07-Areas/06-Organization/Products/
- Project links: [[ProjectName]], one file per project in 03-Projects/ (supports type: project and type: workstream)
- People links: [[FirstName-LastName]], one file per person in 04-People/

### Interactions (05-Interactions/YYYY/)
- Interaction files: YYYY-MM-DD-[type]-[topic].md in year subfolders
- Interaction types via `interaction-type` frontmatter: meeting, email, async
- Meeting subtypes via `meeting-type`: 1on1, steerco, sync, general
- 1on1 meetings have an additional `person:` frontmatter field: wikilink to the other person in the 1on1 (e.g., `person: "[[FirstName-LastName]]"`)
- Email notes: YYYY-MM-DD-email-[topic].md
- Async notes (Slack/Teams): YYYY-MM-DD-async-[topic].md

### Periodic notes (01-Daily/YYYY/)
All periodic notes live in the same folder with year subfolders:
- Daily notes: `YYYY-MM-DD.md` (type: daily)
- Weekly reviews: `YYYY-WXX-weekly.md` (type: weekly-review)
- Monthly reviews: `YYYY-MM-monthly.md` (type: monthly-review)

Weekly review frontmatter:
```yaml
type: weekly-review
week: 10
period-start: YYYY-MM-DD
period-end: YYYY-MM-DD
```

Monthly review frontmatter:
```yaml
type: monthly-review
month: YYYY-MM
period-start: YYYY-MM-DD
period-end: YYYY-MM-DD
```

### Organization
- Organization entities in 07-Areas/06-Organization/ with subfolders: Products/, Markets/, Departments/, Teams/, Partners/, Segments/
- Segments (B2B, EU, SMB) are hub pages with Dataview queries; link via [[SegmentName]] or #segment/name
- Tags hierarchy: #meeting/1on1 #meeting/steerco #meeting/sync #project/alpha

### Action items and tracking
- **Single source of truth**: Action item checkboxes (`- [ ]`) live ONLY in interaction notes (05-Interactions/) and project files (03-Projects/). Never duplicate checkboxes in daily notes.
- **Daily notes and reviews**: Reference actions as plain text with a link to the source: `- [[Owner-Name]] description → [[source-note|Source]]`. No checkboxes. This prevents double-counting in Dataview queries and split completion state. Same rule applies to weekly and monthly reviews.
- **Interaction/project notes**: Use standard markdown checkboxes: `- [ ] [[Owner-Name]] description [due:: YYYY-MM-DD] [delegated-by:: [[Sam-Rivera]]] [source:: [[meeting-or-project]]] [created:: YYYY-MM-DD]`
- **`[created::]` is stamped automatically** by `write-notes.py` at ingestion (date copied from the note's `date:` frontmatter). For manually-authored tasks, add it yourself or let `/w-task-audit --fix --backfill-created` stamp it.
- **`[demoted::]`** marks tasks the forgettability filter stripped from checkbox to plain bullet. Format: `- [[Owner]] description [demoted:: forgettability] [created:: YYYY-MM-DD] [source:: ...]`. These items are NOT counted in My-Tasks dashboards or `/w-1on1` prep. They remain searchable in the source note. To re-promote a wrongly-demoted item: delete the `[demoted::]` field and prepend `- [ ]` back to the line. The weekly review (`/w-review`) lists last week's demotions as a sanity check.
- **Hygiene at write time**: `write-notes.py` enforces the task ownership matrix. Non-Sam tasks from group settings (>5 attendees, no boss/stakeholder VIP) auto-convert to plain bullets. Small meetings, 1on1s, and sent emails auto-add `[delegated-by:: [[Sam-Rivera]]]` to non-Sam tasks. Boss-chain or stakeholder VIPs bypass the filter entirely.
- Action owners are always wikilinks (not @mentions) so they create backlinks in Obsidian
- **Sam-relevant only**: Only extract actions where Sam is the actor, or where Sam explicitly delegated/requested something from someone else. Skip actions between third parties that Sam was merely CC'd on or observing.
  - Sam owns it: `- [ ] [[Sam-Rivera]] do X [source:: ...]`
  - Delegated by Sam: `- [ ] [[Person]] do X [delegated-by:: [[Sam-Rivera]]] [source:: ...]`
  - Owed to Sam: someone committed to deliver something to Sam
  - Do NOT extract: tasks between other people Sam was CC'd on, internal tasks of other teams mentioned in FYI emails, generic actions from threads Sam is only observing
- Decisions log format: | Date | Decision | Rationale | Owner | Status | (in project and product files)
- Dependencies in projects: - [ ] [dep:: [[Target]]] description [status:: open]
- Risks in projects: - [risk:: description] [impact:: high/medium/low] [mitigation:: action]

### File handling
- Templates live in _templates/, always use them for new notes
- Raw files (PDF, DOCX, etc.) go to _attachments/ after processing transcripts
- Documents (PDF/DOCX/PPTX/XLSX/HTML) are deleted from inbox after conversion
- Emails (.txt) are deleted from inbox after processing (OneDrive originals untouched)
- Processed documents go to 08-Reference/
- Everything enters through 00-Inbox/ first

### Archive
- At quarter end, move completed projects and past-quarter OKR files to 09-Archive/YYYY-QN/
- Keep people and product files forever
- Archive interaction notes older than 2 quarters unless they have unresolved actions
