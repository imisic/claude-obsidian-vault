<%*
const date = tp.date.now("YYYY-MM-DD");
await tp.file.rename(date);
await tp.file.move(`00-Inbox/${date}`);
-%>
---
date: <% date %>
type: manual-note
week: <% tp.date.now("ww") %>
---

# <% tp.date.now("dddd, MMMM D") %>

## Today's focus
1.
2.
3.

## Capture
<!--
Drop tasks or quick thoughts here. /w-daily processes this section:
  - [ ] thing  → routed to 07-Areas/My-Tasks.md as a tracked task
  - thing      → routed to ## Notes below as an untracked note
Section is cleared after processing. Comments (HTML) are preserved.
-->

---

## Notes

