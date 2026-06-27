<%*
const quarter = await tp.system.prompt("Quarter (e.g. 2026-Q2)");
await tp.file.rename(quarter);
await tp.file.move(`07-Areas/OKRs/${quarter}`);
-%>
---
type: okr
quarter: <% quarter %>
status: active
---

# OKRs - <% quarter %>

## Objective 1:

### KR1: [description]
- **Target:** [metric]
- **Current:** [value] (updated YYYY-MM-DD)
- **Status:** on-track
- **Projects:** [[Project-A]]

### KR2: [description]
- **Target:** [metric]
- **Current:** [value] (updated YYYY-MM-DD)
- **Status:** on-track
- **Projects:**

### KR3: [description]
- **Target:** [metric]
- **Current:** [value] (updated YYYY-MM-DD)
- **Status:** on-track
- **Projects:**

## Linked projects
![[okr-projects.base]]

## Notes
