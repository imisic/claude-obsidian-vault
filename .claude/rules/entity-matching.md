## Entity Matching Rules

Single source of truth for resolving names, emails, and terms to vault wikilinks.

### Registry location
`_db/entity-registry.json`: ALWAYS read this before matching. Never hardcode entity data.

### Verification
Entity matching follows the anti-fabrication and no-false-absence rules in `.claude/rules/verification.md`. The operational consequence here: when you cannot confidently place a name, return NO MATCH and let `create-stubs.py` handle it. Never guess between two people who share a first name (step 8), and never assert a person is absent from the registry without running the full lookup below.

### Matching process (case-insensitive)

1. **Normalize input**: lowercase, trim whitespace
2. **Structured transcript speaker mapping**: if input is a `SPEAKER_NN` or `voice-NNN` label from a structured transcript and the transcript's manifest entry has a `speakers_map` with a matching key, resolve via the mapped value. Mapped values that are themselves raw labels (`voice-NNN`, `SPEAKER_NN`, `Unknown`) are not yet useful: keep the input as-is in body text until the recorder's voice profile matcher emits a display name. Mapped values that look like a display name (`FirstName-LastName` or similar) flow through the rest of this list.
3. **Check registry**: compare against all `name`, `aliases`, and `emails` fields
4. **Handle "Last, First" format**: split on comma, reverse, rejoin with hyphen → check registry
5. **Handle email-only senders**: match `local-part` (before @) against registry email fields
6. **Handle display names in angle brackets**: extract `"Display Name" <email>`, match display name first, fall back to email
7. **Participant cross-reference (preferred for body-text first names)**: if a bare first name appears in body text or a quoted reply, scan the note's own frontmatter wikilinks (`from`, `to`, `cc`, `attendees`, `person`) for one whose slug starts with `<FirstName>-`. If exactly one participant matches, use that wikilink. This is a high-confidence match because the person is verifiably in the conversation. If multiple participants share the first name, fall through to step 8 (don't guess between them). This step runs BEFORE the registry-wide fuzzy lookup because participant presence is much stronger evidence than registry rarity.
8. **Fuzzy first name (registry-wide)**: if step 7 didn't resolve and a single first name appears, check whether it uniquely matches one person in the registry. Only use if unambiguous: if multiple people share the first name, return NO MATCH (do not guess).
9. **Fallback to filesystem**: check filenames in `04-People/` with case-insensitive compare
10. **No match found**: create wikilink using best-guess `FirstName-LastName` format. Add the unresolved name to `unresolved-entities:` list in the note's frontmatter for the master command to review. Do NOT use HTML comments (`<!-- -->`) as they render visibly in Obsidian.

#### Worked example
Email with `from: [[Sam-Rivera]]`, `to: [[Vikram-Rao]]`, `cc: [[Bruno-Silva]]`. Body says "Following up on what Vikram and I discussed last week..."
- Step 3 (registry exact): "Vikram" matches 3 entries (Vikram, Anil-Kumar, Vikram-Rao) → ambiguous, skip
- Step 7 (participant cross-reference): `to` contains `[[Vikram-Rao]]` whose slug starts with `Vikram-` → use `[[Vikram-Rao]]`. Done.
- Step 8 (registry fuzzy) would have returned NO MATCH since 3 Vikrams exist. Step 7 prevents this and correctly resolves to the Vikram who's actually in the email.

### Entity types

| Type | Registry key | Wikilink format | Example |
|------|-------------|-----------------|---------|
| People | `people` | `[[FirstName-LastName]]` | `[[Sam-Rivera]]` |
| Products | `products` | `[[Abbreviation]]` | `[[Orion]]`, `[[Nimbus]]` |
| Projects | `projects` | `[[ProjectName]]` | `[[Redesign-and-Accessibility]]` |
| Markets | `markets` | `[[CountryCode]]` | `[[DE]]`, `[[FR]]` |
| Segments | `segments` | `[[SegmentName]]` | `[[B2B]]`, `[[EU]]` |
| Teams | `teams` | `[[Group-Name]]` or `[[VS-Name]]` | `[[Group-Legacy-Project]]` |

### Email domain → company mapping
When creating new person stubs from email addresses, infer company from domain:
- `@acme.example` or `@acme.onmicrosoft.com` → Acme Corp
- `@acmedigital.example` → Acme Digital
- `@external.acme.example` → Acme Corp (external contractor)
- `@acme-de.example` → Acme Corp
- `@acme-fr.example` → Acme Corp
- `@partner.example` → Partner Co

### Recipient parsing
Power Automate separates multiple recipients with semicolons (`;`), not commas. Split on `;`, trim whitespace, then match each address individually.

### Sam detection
Sam is the vault owner. Match any of:
- `s.rivera@acme.example`
- `sam.rivera@acme.onmicrosoft.com`
- Display name containing "Sam Rivera" or "Sam Rivera"
- `From:` field matching any of the above

When Sam is the sender, set `direction: sent` in frontmatter.

### New entity handling
When an unmatched entity is found during ingestion:
1. `classify-inbox.py` collects unresolved entities in `_db/manifest.json`
2. `create-stubs.py` (run after classification, before agent dispatch) creates stub files in `04-People/`, updates `_db/entity-registry.json`, `_db/email-lookup.json`, and `_db/sanitize-mappings.json`
3. Stub person files get `status: stub` in frontmatter for later enrichment
4. Stubs resolve on the next ingestion run (email-lookup is rebuilt with new entries)

### Resurrection of archived people
When an email arrives from a person whose registry entry has `status: archived`, `create-stubs.py`:
1. Looks for their file in `04-People/_archived/<Slug>.md`
2. If found, moves it back to `04-People/<Slug>.md` (un-archive)
3. Clears the `status: archived` flag from the registry entry
4. Appends a `RESURRECT` row to `_db/people-archive-analysis.csv` for audit
5. Reports the resurrection in the script's JSON output (`resurrected: [...]`)

This means archive isn't permanent: it's "dormant until they reappear." Prevents wikilinks from rotting when an archived person re-surfaces in the inbox.

### Stub creation threshold
Not every unmatched person needs a stub file. Create stubs only for people in **direct interactions**:
- **CREATE stub**: Person appears in `From:`, or in `To:`/`CC:` where total recipients ≤ 5, or is mentioned by name in the email body (not just a CC list)
- **REGISTRY only** (no stub file): Person appears only in mass CC/To lists (> 5 recipients) with no other interaction context. Add to `_db/entity-registry.json` with `"stub": false` so they're recognized in future, but don't clutter `04-People/`
- **SKIP entirely**: Person appears only in quoted thread content from other people's emails (not a direct participant)

### Registry maintenance
- Add new people when discovered during ingestion (with email if available)
- Update emails when seen in new contexts
- Add project aliases as they appear in communications
- Never remove entries, only add or update
