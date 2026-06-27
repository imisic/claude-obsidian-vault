---
name: w-setup
description: One-time setup wizard. Interviews you, then configures the vault to your identity, org, VIP roster, projects, and tools. Re-runnable to adjust.
user-invocable: true
argument-hint: ""
---

# Vault Setup

Tailor this template to the person running it. You interview the user, then write their config. Everything the pipeline needs lives in a few precise places; this skill fills them all in one pass so they never hand-edit Python or JSON.

The split: `apply-setup.py` does the **structured** writes (owner config, registry, bookmarks, `.env`); **you** do the **prose** edits (CLAUDE.md, vip.md, the example notes) afterward, because those are content edits.

## Step 0: Detect prior config

Read `_scripts/utils.py` and find `OWNER_SLUG`. If it is **not** `"Sam-Rivera"`, the vault is already configured. Ask whether to (a) update specific things or (b) reconfigure from scratch. Otherwise this is a fresh setup.

## Step 1: Interview (one topic at a time)

Collect, conversationally (use AskUserQuestion for the multiple-choice ones):

1. **Identity**: full name (e.g. "Alex Kim" → slug `Alex-Kim`), role/title, company/org.
2. **Email addresses**: their work address(es) and any personal address they self-forward from. These detect sent mail and their own speaker turns.
3. **Manager** (optional): name + email. Becomes a `boss-chain` VIP and a person note.
4. **Email domain → company** map: for each domain they receive mail from (e.g. `globex.example`), the company name. Used to label new people.
5. **PM features?** (AskUserQuestion, this is the preset toggle): "Do you want the product-management layer (products, markets, segments, OKRs, steering-committee meeting type)?" If **no**, this stays a general working-notes vault: skip product/market/segment questions and keep wording role-neutral. If **yes**, ask for any products/markets they want seeded.
6. **VIP roster** (optional): people whose mail/meetings should get boosted relevance. For each: name, email (optional), tier (`boss-chain` = their management chain, `stakeholder` = senior peers, `team` = close collaborators). See `.claude/rules/vip.md`.
7. **Projects** (optional): one or more current project names.
8. **Integrations** (AskUserQuestion, each optional): **Plaud** transcript sync? **Windows/OneDrive email capture**? Either can be left off. If they have neither a recorder nor Plaud, transcript/Plaud steps simply no-op on every run, nothing to configure.

Keep it light. They can always re-run this skill or ask you later to add people/projects.

## Step 2: Confirm

Show a compact summary of what will be written (identity, # of VIPs, projects, integrations on/off, PM preset on/off). Get a yes before writing anything.

## Step 3: Write answers + run the scripts

Write the collected answers to `_db/setup-answers.json` (gitignored, so their real emails are never committed) in this shape:

```json
{
  "owner": {"slug": "Alex-Kim", "name": "Alex Kim", "company": "Globex",
            "personal_emails": ["..."], "work_emails": ["..."], "timezone": "America/New_York"},
  "manager": {"name": "Dana Fox", "email": "dana@globex.example"},
  "domains": {"globex.example": "Globex"},
  "vips": [{"name": "Sam Lee", "email": "...", "tier": "team"}],
  "projects": [{"name": "Project-Zenith"}],
  "pm_features": false,
  "integrations": {"plaud": false, "windows_email": false}
}
```

Then run, in order:

```bash
python _scripts/check-environment.py            # show what's installed + what each tool unlocks
python _scripts/apply-setup.py --vault "$VAULT"  # writes utils.py block, registry, bookmarks, .env
```

Relay the environment report to the user (especially any missing `markitdown` for Office files). Read `apply-setup.py`'s JSON output (`owner_slug`, `manager_slug`, `pm_features`) for the next step.

## Step 4: Prose edits (you do these)

Using the answers and `apply-setup.py`'s output:

1. **`CLAUDE.md`** → rewrite the `## About me` block with their identity, role, manager, emails. If `pm_features` is false, make the role wording neutral (drop product-manager-specific phrasing).
2. **`.claude/rules/vip.md`** → regenerate the tier-definitions table from the new `_db/entity-registry.json` (one row per tier; list the people in each). Update the prose people-names to match.
3. **`04-People/Sam-Rivera.md`** → rename to `<owner_slug>.md` and replace the persona's name/role/company/email in frontmatter + body with theirs. Keep the "this is you, the owner" framing.
4. **`04-People/Jordan-Lee.md`** → if a manager was given, rename to `<manager_slug>.md` and reframe as them; otherwise delete it.
5. **VIP stubs**: for each VIP without a person note, optionally create a minimal stub in `04-People/` (or leave them registry-only; ingestion will stub them on first contact).
6. **`03-Projects/Project-Alpha.md`** → if they named a project, rename/reframe to it; otherwise leave it as the labeled example.
7. If `pm_features` is false, you may delete the PM-only org seeds they will not use; do not delete the templates (`_templates/` is harmless to keep).

## Step 5: Validate

```bash
python _scripts/build-email-lookup.py --vault "$VAULT"
python _scripts/build-person-index.py --vault "$VAULT"
grep -rIn "Sam-Rivera\|Sam Rivera\|Acme" "$VAULT" --include="*.md" --exclude-dir=.git | grep -v ".claude/" || echo "no persona leftovers in content"
```

The grep excludes `.claude/` because the skill docs use "Sam" as a teaching example on purpose; those stay. Report any leftovers in the user's own notes/config.

## Step 6: Report + set expectations

Summarize what changed, then tell the user plainly:

> These skills were tuned to one person's specific setup. On a different machine or workflow some steps may run slowly or surface errors the first few times. That is expected. After your first couple of `/w-daily` runs, just ask me (in plain language) to fix, simplify, remove, or adapt any skill or script to how you actually work. The system is meant to be reshaped, not used as-is.
