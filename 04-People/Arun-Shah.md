---
name: Arun Shah
company: Acme Corp
role: Data
market:
team: New-Ventures
email: arun.shah@acme.example
products: ["[[Nimbus]]"]
type: person
status: active
---

# Arun Shah

> [!example] Example person note (a `team` VIP). See `.claude/rules/vip.md`. Fictional demo data. Replace or delete. See README → "Example content".

## Context
- **Role:** Data, [[New-Ventures]]
- **Company/Market:** Acme Corp
- **How we work together:** Bridges [[Orion]] and the [[Nimbus]] data platform; owns the sync load test.

## Open action items
```dataview
TASK
WHERE !completed
WHERE contains(text, this.file.name)
WHERE !contains(file.path, "01-Daily") AND !contains(file.path, "_templates") AND !contains(file.path, "09-Archive")
```

## Interactions
```dataview
LIST
FROM "05-Interactions"
WHERE contains(file.outlinks, this.file.link)
SORT date DESC
LIMIT 10
```

## Notes
