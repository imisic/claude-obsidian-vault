---
name: Mia Fischer
company: Acme Corp
role: Business Development
market:
team: New-Ventures
email: mia.fischer@acme.example
products: ["[[Orion]]"]
type: person
status: active
---

# Mia Fischer

> [!example] Example person note (a `team` VIP, core [[Project-Alpha]] collaborator). See `.claude/rules/vip.md`. Fictional demo data. Replace or delete. See README → "Example content".

## Context
- **Role:** Business Development, [[New-Ventures]]
- **Company/Market:** Acme Corp
- **How we work together:** Delivery partner on [[Project-Alpha]]; owns [[Partner-Onboarding]].

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
