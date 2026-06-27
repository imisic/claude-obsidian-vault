## VIP Flagging System

Lightweight VIP awareness for interactions involving important people. Source of truth for VIP tiers is `_db/entity-registry.json` (`"vip"` field on people entries).

### Tier definitions

| Tier | Meaning | People |
|------|---------|--------|
| `boss-chain` | Management chain above Sam | Jordan Lee (direct), Morgan Hayes (skip), Patricia Vance (skip-skip) |
| `stakeholder` | Senior peers / cross-functional stakeholders | Elena Rossi (SPM lead, Strategy), David Klein (VP Partnerships), Noah Bauer (Regional MD), George Pappas (Regional MD) |
| `team` | New Ventures core collaborators on Project-Alpha | Mia Fischer (BD partner), Raj Patel (Engineering lead), Sofia Costa (Design lead), Lukas Berger (Strategy), Piotr Nowak (Scouting), Arun Shah (Data) |

Non-VIP people have no `vip` field (not null or false, field is absent). Other New Ventures members (Tom Becker on Project-Beta, Lena Wolf on Project-Gamma, etc.) are NOT VIP. Sam collaborates with them but they are not in his direct delivery loop.

### Relevance boost (implemented in `_scripts/utils.py:apply_vip_boost()`)

Called by `classify-inbox.py` during `--resolve-entities`. Agents receive the boosted relevance in pre-generated frontmatter and do NOT re-apply boost logic.

| VIP tier | Position | Adjustment |
|----------|----------|------------|
| `boss-chain` | From/To (not just CC) | LOW→MEDIUM, MEDIUM→HIGH |
| `boss-chain` | CC only | LOW→MEDIUM |
| `stakeholder` | From/To (not just CC) | LOW→MEDIUM |
| `stakeholder` | CC only | No change |
| `team` | Any position | No change (high-volume daily collab, judge on content) |

### Frontmatter on interaction notes

When any participant (From/To/CC for emails, attendees for meetings) has a `vip` field, add:

```yaml
vip-involved:
  - boss-chain          # list all distinct tiers present
tags:
  - vip/boss-chain      # enables Obsidian search/filter
```

- Only include tiers that are actually present among participants
- Multiple tiers possible (e.g., boss-chain person + team member in same meeting)
- When multiple VIP rules apply simultaneously, apply the rule that results in the highest tier
- Tags use `vip/` prefix for Obsidian tag hierarchy

### Daily briefing markers

No new sections. Prefix markers on existing lines:

**Key emails table:**
```
| | Topic | Summary | Source |
|-|-------|---------|--------|
| **!** | Subject | Boss flagged issue... | [[...]] |
| * | Subject | Julia shared plan... | [[...]] |
| | Subject | Regular update... | [[...]] |
```
- `**!**` = boss-chain involved
- `*` = stakeholder involved
- (blank) = team or no VIP

**Action items**: same prefix on VIP-related lines:
```
- **!** [[Boss-Name]] clarify situation → [[source|Source]]
- * [[Stakeholder-Name]] share Q2 plan → [[source|Source]]
```

### Timing note

VIP detection uses the registry as it exists at processing time. If a person is first encountered in an email before being added to the registry (or before their `vip` field is set), they will NOT receive a relevance boost for that run. The boost applies from the next ingestion run onward, after the registry is updated. This is by design: don't retroactively re-score old emails.

### Maintenance

- Update tiers in `_db/entity-registry.json` when org changes happen
- When someone leaves, remove their `vip` field from the registry and update the tier definitions table above
- When new reports/bosses join, add `vip` field to their registry entry and update this doc
