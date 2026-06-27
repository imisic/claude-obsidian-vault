<%*
const people = app.vault.getFiles()
  .filter(f => f.path.startsWith("04-People/") && f.extension === "md")
  .map(f => f.basename)
  .sort();
const person = await tp.system.suggester(
  people.map(p => p.replaceAll("-", " ")),
  people,
  false,
  "Select person"
);
if (!person) { await app.vault.trash(tp.config.target_file, true); return; }
const date = tp.date.now("YYYY-MM-DD");
const filename = `${date}-1on1-${person}`;
await tp.file.rename(filename);
await tp.file.move(`00-Inbox/${filename}`);
-%>
---
date: <% date %>
type: meeting
interaction-type: meeting
meeting-type: 1on1
person: "[[<% person %>]]"
summary:
project:
source-file:
---

# 1on1, [[<% person %>]], <% date %>

## Open tasks with [[<% person %>]]
```dataview
TASK
WHERE !completed AND contains(text, "[[<% person %>]]")
AND !contains(file.path, "01-Daily") AND !contains(file.path, "_templates") AND !contains(file.path, "09-Archive")
SORT file.ctime DESC
```

## Previous 1on1s
```dataview
LIST
FROM "05-Interactions"
WHERE meeting-type = "1on1" AND person = [[<% person %>]] AND file.name != this.file.name
SORT date DESC
LIMIT 5
```

---

## Discussion
-

## Actions
- [ ]

## Next time
-

## Actions checkpoint
> [!warning] Before closing: did you capture all actions with [[Owner]] and [due:: date]?
