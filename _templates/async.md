<%*
const topic = await tp.system.prompt("Async topic (Slack/Teams thread)");
if (!topic) { await app.vault.trash(tp.config.target_file, true); return; }
const date = tp.date.now("YYYY-MM-DD");
const slug = topic.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "");
const filename = `${date}-async-${slug}`;
await tp.file.rename(filename);
await tp.file.move(`05-Interactions/${date.substring(0,4)}/${filename}`);
-%>
---
date: <% date %>
type: async
interaction-type: async
summary:
project:
participants: []
source-file:
---

# <% topic %>, <% date %>

## Context
-

## Key points
-

## Decisions
-

## Actions
- [ ]
