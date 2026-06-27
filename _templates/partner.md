<%*
const name = await tp.system.prompt("Partner/vendor name");
await tp.file.rename(name);
await tp.file.move(`07-Areas/06-Organization/Partners/${name}`);
-%>
---
name: <% name %>
type: partner
partner-type:
contract-owner:
---

# <% name %>

## Overview
- **Type:** vendor / agency / consultancy
- **Contract owner:** [[person]]
- **Key contacts:** [[person]]

## Engagement
- **Products:** [[product]]
- **Projects:** [[project]]

## Notes
