---
name: Sam Rivera
company: Acme Corp
role: Senior Product Manager
market:
team: New Ventures
email: sam.rivera@example.com
products: []
type: person
status: active
---

# Sam Rivera

> [!example] Example owner note (the fictional persona used throughout the template). Replace it with your own person note (or rename the file to your `FirstName-LastName`) and update `_db/entity-registry.json` plus the `## About me` block in `CLAUDE.md`. See README → "Example content".

## Context
- **Role:** Senior Product Manager, New Ventures
- **Company/Market:** Acme Corp
- **How we work together:** This is you, the vault owner.

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
