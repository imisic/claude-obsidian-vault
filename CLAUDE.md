# Vault Context for Claude Code

This is an Obsidian vault paired with Claude Code skills that automate a knowledge worker's working notes: email ingestion, meeting transcripts, document processing, daily briefings, and periodic reviews. It ships with an optional product-management preset (products, markets, OKRs); turn it off in `/w-setup` for a general working-notes vault. This file gives Claude the context it needs to operate the vault.

> This is a sanitized, shareable copy of a real working system. Names, companies, and data are fictional examples (persona "Sam Rivera" at "Acme Corp"). See the README's Configuration section to make it yours.

## About me (CONFIGURE THIS)

Replace this block with your own details. The example persona is:

- Sam Rivera, Senior Product Manager at Acme Corp
- Works in the "New Ventures" group
- Primary email `sam.rivera@example.com`; work `s.rivera@acme.example`
- Direct manager: [[Jordan-Lee]]

Run `/w-setup` to fill these in (and the rest) automatically. To do it by hand, these details also live in a few precise places that the automation reads (see README → Configuration): the `OWNER CONFIG` block in `_scripts/utils.py` (single source of truth for slug/name/company/emails/timezone), `_db/entity-registry.json`, and `.claude/rules/vip.md`.

## Vault navigation

- Drop files → `00-Inbox/` (processed by `/w-daily`)
- Daily, weekly, monthly notes → `01-Daily/YYYY/`
- Projects & workstreams → `03-Projects/`
- People → `04-People/`
- All interactions (meetings, emails, async) → `05-Interactions/YYYY/`
- Organization (Products, Markets, Departments, Teams, Partners, Segments) → `07-Areas/06-Organization/`
- Areas, OKRs, dashboards → `07-Areas/`
- Processed reference docs → `08-Reference/`
- Archive → `09-Archive/`
- Raw files stay in `_attachments/`

## Key skills (all prefixed `w-`)

- `/w-setup`: one-time setup wizard: interviews you, then configures the vault to your identity, org, and tools (re-runnable)
- `/w-daily`: master skill: ingest inbox, create daily note + briefing
- `/w-review [period]`: unified reviews: `weekly`, `monthly`, `monthly 2026-02`, `last 30 days`, `project:Name last 2 weeks`
- `/w-project-status [name]`: summary of a project's recent activity
- `/w-1on1 [person]`: prep for a 1on1: last meeting notes, open items, talking points
- `/w-prep [person/topic]`: conversation prep or a "what I did" recap
- `/w-task-audit`: action-item hygiene audit

## Conventions

- Dates: ISO format (`2026-03-04`)
- People links: `[[FirstName-LastName]]`
- Project links: `[[ProjectName]]`
- Interaction files: `YYYY-MM-DD-[type]-[topic].md` (e.g. `2026-03-05-email-budget-approval.md`)
- Tags: `#meeting/1on1`, `#meeting/steerco`, `#meeting/sync`, `#project/name`
- All frontmatter in YAML
- Interaction types via `interaction-type` frontmatter: meeting, email, async

## Obsidian conventions

- Use only valid Obsidian-compatible syntax: wikilinks `[[Name]]` for cross-references, no HTML comments in note bodies (they render visibly), and always convert `@Name` mentions to `[[Name]]` wikilinks.
- Do not invent Obsidian features. Verify syntax exists before using it.

## System specification

- **Full spec:** `.claude/SYSTEM.md`, complete reference for architecture, data flows, skills, rules, templates, scripts, database files, and Obsidian config.
- **Read it** before modifying any skill, rule, template, config, or data flow.
- **Update it** after any change to skills, rules, scripts, configs, content types, routing, or schemas.

## Architecture (summary)

- Skills orchestrate and execute; rules define standards (all in `.claude/`).
- `/w-daily` is the single entry point for all ingestion (no separate ingest command).
- Daily briefings are AI-generated plain text, not Dataview queries.
- The entity registry (`_db/entity-registry.json`) is the source of truth for people/product/project linking.
- People stubs are auto-created from emails; projects are NEVER auto-created (flag-only).
- Owner identity (slug, name, company, emails, timezone) is configured in one place: the `OWNER CONFIG` block in `_scripts/utils.py`. Every script imports from there.
- The product-management layer (products, markets, segments, OKRs, steerco) is an optional preset, toggled in `/w-setup`.
- Processor skills (email, doc, transcript) run on Sonnet for efficiency; synthesis skills (review, 1on1-prep, project-status) run on Opus for quality.
- Email threads are consolidated: same-thread emails merge into fewer notes.

## Batch processing

- When processing files in bulk, verify the first 2-3 results before continuing with the full batch.
- Never trust that shell globs or file listings succeeded. Explicitly check file counts and show sample output.
- When merging entity data, never merge records with the same first name but different identities. Confirm ambiguous matches.

## Conciseness principle

All AI-generated outputs (briefings, reviews, prep notes, status reports) are a **scan layer**. They surface what matters and link to reference files for detail. Never try to be self-contained. The reader has 2 minutes, not 20.

- Briefings and reviews point to interaction notes via wikilinks. Don't repeat what's in them.
- If a bullet needs more than 2 lines, split it.
- Prefer tables and terse bullets over prose paragraphs.
- High bar for actions and decisions: false positives are worse than omissions. A `## Decisions` entry must be an explicit, committed decision. An action must be owner-relevant and pass the forgettability test. When in doubt, drop it.
