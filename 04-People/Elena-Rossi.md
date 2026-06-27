---
name: Elena Rossi
company: Acme Corp
role: SPM Lead, Strategy
market:
team: Product-Strategy
email: elena.rossi@acme.example
products: []
type: person
status: active
---

# Elena Rossi

> [!example] Example person note (a `stakeholder` VIP, senior peer). See `.claude/rules/vip.md`. Fictional demo data. Replace or delete. See README → "Example content".

## Context
- **Role:** SPM Lead, Strategy
- **Company/Market:** Acme Corp
- **How we work together:** Cross-functional stakeholder on [[Project-Alpha]]; peer review of strategy.

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
