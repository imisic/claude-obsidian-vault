<%*
const name = await tp.system.prompt("Team name");
await tp.file.rename(name);
await tp.file.move(`07-Areas/06-Organization/Teams/${name}`);
-%>
---
name: <% name %>
type: team
department:
lead:
---

# <% name %>

## Overview
- **Lead:** [[person]]
- **Department:** [[department]]

## Members
- [[person]]

## Responsibilities
-

## Notes
