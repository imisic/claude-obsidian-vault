# Obsidian plugins

This vault is configured to work with a set of Obsidian plugins.

**Claude's automation needs none of them.** The `_scripts/` pipeline and the `/w-*` skills read and write plain Markdown. You can run `/w-daily`, ingest an inbox, and generate briefings with zero plugins installed. The plugins are for *your* experience reading and authoring notes in Obsidian, not for the agent.

**Neither the plugin code nor its settings travel with this repo.** `.obsidian/plugins/` is gitignored, on purpose: it holds third-party code plus each plugin's `data.json`, which can carry personal config (a Git remote URL, tokens, local paths). What ships is the *enabled list* (`.obsidian/community-plugins.json`) so Obsidian knows which plugins you intend to use, plus the core-plugin and theme config files. So after cloning you install the plugins yourself, and you reapply a few vault-specific settings (below).

## Installing

1. Open the vault, go to Settings → Community plugins, and turn off Restricted Mode.
2. For each plugin in the list, Browse → install → enable. Reload Obsidian.
3. Enable the core **Bases** plugin under Settings → Core plugins (it is recent, so update Obsidian if you don't see it).

Until Dataview and Bases are on, the dashboard and database blocks render as empty or as raw code. That is expected and resolves once the plugins load.

## What each one is for

| Plugin | Role | Tier |
|--------|------|------|
| **Dataview** | Renders the dashboards, the per-person interaction views, the segment hub pages, and task rollups. Without it those blocks show as raw code. | Essential |
| **Bases** (core, not community) | Powers the `_bases/` database views. Enable under Core plugins. | Essential for those views |
| **Templater** | Note templates in `_templates/` (meeting, 1on1, daily). Needed if you create notes inside Obsidian rather than only via the inbox. | Recommended |
| **Daily Notes** (core) | Pre-configured and shipped: the daily-note button creates a note in `00-Inbox/` from `_templates/daily-note`, which `/w-daily` then routes into `01-Daily/`. Works out of the box. | Recommended |
| **Periodic Notes** | Weekly and monthly note navigation and calendar integration on top of the `01-Daily/` structure. | Recommended |
| **Tasks** | Richer rendering and querying of the `- [ ]` action items. The action data itself is plain Dataview fields, so this is presentation. | Recommended |
| **Calendar** | Month sidebar for jumping between daily and periodic notes. | Optional |
| **QuickAdd** | Capture macros for fast note creation. | Optional |
| **Omnisearch** | Better full-text search than the built-in. | Optional |
| **Linter** | Tidies Markdown formatting on save. | Optional |
| **Icon Folder** | Folder icons in the file tree. Cosmetic. | Optional |
| **Homepage** | Opens a landing note when the vault launches. Point it at `07-Areas/Dashboard` to open the cockpit on launch. | Optional |
| **Obsidian Git** | Version-control the vault from inside Obsidian. One way to back up or sync. | Optional |

## Settings you reapply after installing

Because the per-plugin `data.json` is gitignored, the community plugins come up with default settings. Match them to the vault's conventions (see `.claude/rules/obsidian-conventions.md` for the canonical folder and naming scheme):

- **Templater** → set the template folder to `_templates/`.
- **Periodic Notes** → point the daily, weekly, and monthly folders and formats at the vault's scheme (`01-Daily/YYYY/`, `YYYY-WXX-weekly`, `YYYY-MM-monthly`).
- **Tasks** → optional; a `task-statuses.css` snippet ships under `.obsidian/snippets/` for custom checkbox states if you enable it.
- **Homepage** → optional; set the homepage to `07-Areas/Dashboard` so the cockpit opens on launch.

What does ship and needs no setup: the core **Daily Notes**, **Templates**, and **Bases** config, the AnuPpuccin theme, and the excluded-folder filters that gray out the `_*` machinery folders in the file tree.
