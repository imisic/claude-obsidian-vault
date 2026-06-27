---
name: Raj Patel
company: Acme Corp
role: Engineering Lead
market:
team: New-Ventures
email: raj.patel@acme.example
products: ["[[Orion]]"]
type: person
status: active
---

# Raj Patel

> [!example] Example person note (a `team` VIP). See `.claude/rules/vip.md`. Fictional demo data. Replace or delete. See README → "Example content".

## Context
- **Role:** Engineering Lead, [[New-Ventures]]
- **Company/Market:** Acme Corp
- **How we work together:** Owns the [[Orion]] build; weekly engineering sync.

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
