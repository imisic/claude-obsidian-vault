---
type: dashboard
---

# Open Tasks

> [!example] Global open-task view. A Dataview query gathering every unchecked `- [ ]` action item across interactions, projects, and `My-Tasks`. Read-only aggregator: the checkboxes live in their source notes, this note just surfaces them. Requires the Dataview community plugin. Fictional demo data. See README → "Example content".

Everything still open across the vault, soonest due first. Check items off in their source note (the link), not here.

```dataview
TASK
WHERE !completed
WHERE !contains(file.folder, "09-Archive")
WHERE !contains(file.folder, "_templates")
SORT due ASC
```
