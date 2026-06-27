<%*
const projects = app.vault.getFiles()
  .filter(f => f.path.startsWith("03-Projects/") && f.extension === "md")
  .map(f => f.basename)
  .sort();
const project = await tp.system.suggester(projects, projects, false, "Select project");
if (!project) { await app.vault.trash(tp.config.target_file, true); return; }
const date = tp.date.now("YYYY-MM-DD");
const filename = `${date}-steerco-${project}`;
await tp.file.rename(filename);
await tp.file.move(`00-Inbox/${filename}`);
-%>
---
date: <% date %>
type: meeting
interaction-type: meeting
meeting-type: steerco
summary:
project: "[[<% project %>]]"
attendees: []
source-file:
---

# SteerCo, [[<% project %>]], <% date %>

## Open tasks for [[<% project %>]]
```dataview
TASK
WHERE !completed AND contains(text, "[[<% project %>]]")
AND !contains(file.path, "01-Daily") AND !contains(file.path, "_templates") AND !contains(file.path, "09-Archive")
SORT file.ctime DESC
```

## Previous SteerCos
```dataview
LIST
FROM "05-Interactions"
WHERE meeting-type = "steerco" AND project = [[<% project %>]] AND file.name != this.file.name
SORT date DESC
LIMIT 5
```

---

## Attendees
-

## Agenda
1.

## Decisions made
-

## Actions
- [ ]

## Next SteerCo
- Date:
- Proposed agenda:

## Actions checkpoint
> [!warning] Before closing: did you capture all actions with [[Owner]] and [due:: date]?
