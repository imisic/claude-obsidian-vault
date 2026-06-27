---
name: David Klein
company: Acme Corp
role: VP Partnerships
market:
team: Partnerships
email: david.klein@acme.example
products: []
type: person
status: active
---

# David Klein

> [!example] Example person note (a `stakeholder` VIP, owns the [[Northwind]] contract). See `.claude/rules/vip.md`. Fictional demo data. Replace or delete. See README → "Example content".

## Context
- **Role:** VP Partnerships
- **Company/Market:** Acme Corp
- **How we work together:** Owns the [[Northwind]] partnership for [[Project-Alpha]].

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
