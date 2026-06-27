<%*
const name = await tp.system.prompt("Product short name (short identifier)");
const fullName = await tp.system.prompt("Full name");
await tp.file.rename(name);
await tp.file.move(`07-Areas/06-Organization/Products/${name}`);
-%>
---
name: <% name %>
platforms: []
status: active
markets: []
lead:
type: product
aliases:
  - <% fullName %>
---

# <% name %>

## What it is
One paragraph max.

## Value streams
-

## Key stakeholders
- [[person]]

## Current status
**As of <% tp.date.now("YYYY-MM-DD") %>:**

## Open items
- [ ]

## Projects
![[product-overview.base#Projects]]

## People
![[product-overview.base#People]]

## Decisions log
| Date | Decision | Rationale | Owner | Status |
|-|-|-|-|-|

## Meeting notes
![[project-interactions.base]]

## History
