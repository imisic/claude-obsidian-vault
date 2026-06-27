---
name: Hannah Vogel
company: Northwind Logistics
role:
market:
team:
email: hannah.vogel@northwind.example
products: []
type: person
status: stub
---

# Hannah Vogel

> [!example] Example **stub** person note (this is exactly what `create-stubs.py` auto-creates when a new external contact appears in the inbox; `status: stub` marks it for later enrichment). Fictional demo data. Replace or delete. See README → "Example content".

## Context
- **Role:** (stub, auto-created from an email/meeting, not yet enriched)
- **Company/Market:** Northwind Logistics (external partner)
- **How we work together:** Northwind-side contact for the [[Orion]] fulfillment partnership.

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
