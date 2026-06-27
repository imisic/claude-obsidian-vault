<%*
const title = await tp.system.prompt("Document title");
const date = tp.date.now("YYYY-MM-DD");
const slug = title.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "");
const filename = `${date}-${slug}`;
await tp.file.rename(filename);
await tp.file.move(`08-Reference/${filename}`);
-%>
---
date: <% date %>
source-file:
type: reference
summary:
project:
tags: []
---

# <% title %>

*Source: ingested <% date %>*

## Summary

## Key points
-

## Actions identified
- [ ]

## Content

