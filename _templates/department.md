<%*
const name = await tp.system.prompt("Department name");
await tp.file.rename(name);
await tp.file.move(`07-Areas/06-Organization/Departments/${name}`);
-%>
---
name: <% name %>
type: department
parent:
head:
---

# <% name %>

## Overview
- **Head:** [[person]]
- **Parent org:**

## Teams
-

## Key contacts
- [[person]]

## Notes
