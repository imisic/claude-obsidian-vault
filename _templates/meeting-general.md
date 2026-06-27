<%*
const topic = await tp.system.prompt("Meeting topic");
if (!topic) { await app.vault.trash(tp.config.target_file, true); return; }
const date = tp.date.now("YYYY-MM-DD");
const slug = topic.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "");
const filename = `${date}-meeting-${slug}`;
await tp.file.rename(filename);
await tp.file.move(`00-Inbox/${filename}`);
-%>
---
date: <% date %>
type: meeting
interaction-type: meeting
meeting-type: general
summary:
project:
attendees: []
source-file:
---

# <% topic %>, <% date %>

## Attendees
-

## Agenda
1.

## Discussion
-

## Decisions
-

## Actions
- [ ]

## Follow-up
-

## Actions checkpoint
> [!warning] Before closing: did you capture all actions with [[Owner]] and [due:: date]?
