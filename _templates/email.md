<%*
const topic = await tp.system.prompt("Email topic");
if (!topic) { await app.vault.trash(tp.config.target_file, true); return; }
const date = tp.date.now("YYYY-MM-DD");
const slug = topic.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "");
const filename = `${date}-email-${slug}`;
await tp.file.rename(filename);
await tp.file.move(`05-Interactions/${date.substring(0,4)}/${filename}`);
-%>
---
date: <% date %>
type: email
interaction-type: email
from:
to: []
cc: []
subject: <% topic %>
summary:
relevance: medium
direction:
conversation-id:
email-category:
thread-context:
email-thread: []
email-thread-count:
project:
vip-involved: []
tags: []
source-file:
---

# Email: <% topic %>, <% date %>

## Summary
-

## Key points
-

## Actions
- [ ]

## Thread

