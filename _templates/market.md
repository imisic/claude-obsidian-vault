<%*
const code = await tp.system.prompt("Market code (e.g. DE, FR, US)");
const country = await tp.system.prompt("Country name (e.g. Germany, France)");
await tp.file.rename(code);
await tp.file.move(`07-Areas/06-Organization/Markets/${code}`);
-%>
---
name: <% code %>
country: <% country %>
operator:
type: market
aliases:
  - <% country %>
---

# <% code %>

## Overview
- **Country:** <% country %>
- **Operator:**
- **Key contacts:** [[person]]

## Interactions
![[market-overview.base]]

## Notes
