---
name: Morgan Hayes
company: Acme Corp
role: Director, Product & Strategy
market:
team: Product-Strategy
email: morgan.hayes@acme.example
products: []
type: person
status: active
---

# Morgan Hayes

> [!example] Example person note (a `boss-chain` VIP, the owner's skip-level manager). See `.claude/rules/vip.md`. Fictional demo data. Replace or delete. See README → "Example content".

## Context
- **Role:** Director, [[Product-Strategy]] (Sam's skip-level manager)
- **Company/Market:** Acme Corp
- **How we work together:** Steering committees, quarterly reviews.

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
