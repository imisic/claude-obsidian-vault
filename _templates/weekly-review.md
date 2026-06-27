<%*
const weekNum = tp.date.now("ww");
const year = tp.date.now("YYYY");
const fileName = `${year}-W${weekNum}-weekly`;
await tp.file.rename(fileName);
await tp.file.move(`01-Daily/${year}/${fileName}`);
-%>
---
date: <% tp.date.now("YYYY-MM-DD") %>
type: weekly-review
week: <% weekNum %>
period-start: <% tp.date.weekday("YYYY-MM-DD", 0, tp.date.now("YYYY-MM-DD"), "iso") %>
period-end: <% tp.date.now("YYYY-MM-DD") %>
---

# Week <% weekNum %> Review

*Run `/w-review weekly` to generate this review.*

## Summary


## By project


## Action items


## Email threads


## OKR movement


## Next week's focus
1.
2.
3.

## Shareable status

