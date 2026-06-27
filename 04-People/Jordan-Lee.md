---
name: Jordan Lee
company: Acme Corp
role: Head of New Ventures
market:
team: New Ventures
email: jordan.lee@acme.example
products: []
type: person
status: active
---

# Jordan Lee

> [!example] Example person note (a `boss-chain` VIP, the owner's direct manager). See `.claude/rules/vip.md` for how VIP tiers drive relevance scoring and briefing markers. Fictional demo data. Replace or delete. See README → "Example content".

## Context
- **Role:** Head of New Ventures (the owner's direct manager)
- **Company/Market:** Acme Corp
- **How we work together:** Weekly 1on1, quarterly planning.

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
