---
type: dashboard
---

# Dashboard

> [!example] Vault cockpit and home note. Point the **Homepage** community plugin at this file (Homepage settings → set the homepage to `Dashboard`) so it opens on launch. The panels embed the operational Bases beside this note in `07-Areas/` (needs the core **Bases** plugin); the task panel needs **Dataview**. Everything populates from whatever is in the vault. Fictional demo data. See README → "Example content".

**Start the day:** run `/w-daily` in Claude Code to ingest `00-Inbox/` and build the briefing, then open [[2026-03-05|today's note]]. The briefing is *today*; this cockpit is the *standing state*.

## Tasks on the clock

Open action items that carry a due date, soonest first (overdue float to the top). This is the heartbeat panel. The full pile, including items with no due date, lives in [[Open-Tasks]]; your personal capture bucket is [[My-Tasks]].

```dataview
TASK
WHERE !completed AND due
WHERE !contains(file.folder, "09-Archive")
WHERE !contains(file.folder, "_templates")
SORT due ASC
```

## Awaiting review

HIGH-relevance notes still carrying `status: unprocessed`, your manual-review queue.

![[Unprocessed.base]]

## Active projects

![[Active-Projects.base]]

## Recent activity

The default tab lists the latest interactions; the second tab windows to the last 7 days off live data (empty on the frozen March-2026 demo set).

![[This-Week.base]]

## People

![[Recent-People.base]]
