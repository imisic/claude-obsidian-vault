<%*
const month = tp.date.now("YYYY-MM");
const year = tp.date.now("YYYY");
const fileName = `${month}-monthly`;
await tp.file.rename(fileName);
await tp.file.move(`01-Daily/${year}/${fileName}`);
-%>
---
date: <% tp.date.now("YYYY-MM-DD") %>
type: monthly-review
month: <% month %>
period-start: <% month %>-01
period-end: <% tp.date.now("YYYY-MM-DD") %>
---

# <% tp.date.now("MMMM YYYY") %> Review

*Run `/w-review monthly` to generate this review.*

## Headline stats


## By product


## By project


## OKR progress


## Key decisions


## Carry-forward items


## Leadership summary

