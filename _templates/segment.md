<%*
const name = await tp.system.prompt("Segment name");
await tp.file.rename(name);
await tp.file.move(`07-Areas/06-Organization/Segments/${name}`);
-%>
---
name: <% name %>
type: segment
status: active
aliases: []
---

# <% name %>

## Overview
-

## Active projects
```dataview
TABLE status, markets
FROM "03-Projects"
WHERE contains(file.outlinks, this.file.link) OR contains(tags, "#segment/<% name.toLowerCase().replace(/\s+/g, "-") %>")
SORT status ASC
```

## Recent interactions
```dataview
TABLE date, interaction-type, meeting-type
FROM "05-Interactions"
WHERE contains(file.outlinks, this.file.link) OR contains(tags, "#segment/<% name.toLowerCase().replace(/\s+/g, "-") %>")
SORT date DESC
LIMIT 15
```

## Key people
```dataview
LIST
FROM "04-People"
WHERE contains(file.outlinks, this.file.link) OR contains(tags, "#segment/<% name.toLowerCase().replace(/\s+/g, "-") %>")
SORT file.name ASC
```

## Open actions
```dataview
TASK
WHERE !completed AND (contains(text, "[[<% name %>]]") OR contains(text, "#segment/<% name.toLowerCase().replace(/\s+/g, "-") %>"))
AND !contains(file.path, "01-Daily") AND !contains(file.path, "_templates") AND !contains(file.path, "09-Archive")
SORT file.ctime DESC
```

## Notes
