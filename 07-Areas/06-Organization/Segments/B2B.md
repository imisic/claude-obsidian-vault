---
name: B2B
type: segment
status: active
aliases: []
---

# B2B

> [!example] Example segment hub (created from the `segment.md` template). Segment hubs are Dataview-driven, so they stay live as you add notes. Fictional demo data. Replace or delete. See README → "Example content".

## Overview
- Business customers (not consumer). Primary segment for [[Orion]].

## Active projects
```dataview
TABLE status, markets
FROM "03-Projects"
WHERE contains(file.outlinks, this.file.link) OR contains(tags, "#segment/b2b")
SORT status ASC
```

## Recent interactions
```dataview
TABLE date, interaction-type, meeting-type
FROM "05-Interactions"
WHERE contains(file.outlinks, this.file.link) OR contains(tags, "#segment/b2b")
SORT date DESC
LIMIT 15
```

## Key people
```dataview
LIST
FROM "04-People"
WHERE contains(file.outlinks, this.file.link) OR contains(tags, "#segment/b2b")
SORT file.name ASC
```

## Open actions
```dataview
TASK
WHERE !completed AND (contains(text, "[[B2B]]") OR contains(text, "#segment/b2b"))
AND !contains(file.path, "01-Daily") AND !contains(file.path, "_templates") AND !contains(file.path, "09-Archive")
SORT file.ctime DESC
```

## Notes
