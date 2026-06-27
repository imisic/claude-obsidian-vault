<%*
const name = await tp.system.prompt("Person name (FirstName-LastName)");
await tp.file.rename(name);
await tp.file.move(`04-People/${name}`);
-%>
---
name: <% name.replaceAll("-", " ") %>
company:
role:
market:
team:
email:
products: []
type: person
---

# <% name.replaceAll("-", " ") %>

## Context
- **Role:**
- **Company/Market:**
- **How we work together:**

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
