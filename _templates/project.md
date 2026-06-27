<%*
const name = await tp.system.prompt("Project name");
const date = tp.date.now("YYYY-MM-DD");
await tp.file.rename(name);
await tp.file.move(`03-Projects/${name}`);
-%>
---
name: <% name %>
status: active
products: []
markets: []
segment: []
owner:
type: project
quarter:
okr-link:
---

# <% name %>

## What it is
One paragraph max.

## Current status
**As of <% date %>:**

## Key stakeholders
- [[person]]

## Open items
- [ ]

## Dependencies
- [ ] [dep:: [[Other-Project-or-Market]]] description [status:: open]

## Risks & Issues
- [risk:: description] [impact:: high/medium/low] [mitigation:: what we're doing]

## Decisions log
| Date | Decision | Rationale | Owner | Status |
|-|-|-|-|-|

## Meeting notes
![[project-interactions.base]]

## History
