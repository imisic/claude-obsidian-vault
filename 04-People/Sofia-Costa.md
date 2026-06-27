---
name: Sofia Costa
company: Acme Corp
role: Design Lead
market:
team: New-Ventures
email: sofia.costa@acme.example
products: ["[[Orion]]"]
type: person
status: active
---

# Sofia Costa

> [!example] Example person note (a `team` VIP). See `.claude/rules/vip.md`. Fictional demo data. Replace or delete. See README → "Example content".

## Context
- **Role:** Design Lead, [[New-Ventures]]
- **Company/Market:** Acme Corp
- **How we work together:** Owns [[Orion]] UX; leads onboarding flow design for [[Partner-Onboarding]].

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
