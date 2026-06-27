# Speaker resolution: full ruleset

Load this when the transcript body contains non-trivial speaker labels.

## The one principle

You may **normalize a name that is actually present** to its vault wikilink: the same person shows up as `s.rivera@acme.example`, `Sam.Rivera`, `Sam Rivera`, or `Sam-Rivera`, and all of them resolve to `[[Sam-Rivera]]`. That is the only kind of "hunting" allowed: matching a *stated* name across formats.

You may **NOT invent an identity for an anonymous speaker.** A `Speaker 6` / `SPEAKER_03` / `voice-007` / `Unknown` label with no name attached stays anonymous. Do not infer who they are from biographical clues ("mentioned a job at Globex", "spa attendant in the US"), from who "usually attends" this meeting, or by scanning the registry / people files for a plausible fit. A wrong name is worse than no name, and the verbatim transcript stays in `_attachments/`. If Sam later maps that speaker, it reprocesses cleanly.

Known speakers are mapped upstream (Sam's curated `_db/plaud-speaker-map.json` + header resolution by the classifier). Your job is not to rebuild the roster; it is to summarize, normalizing only the names the transcript already gives you.

## Speaker label formats

| Format | Example |
|--------|---------|
| `Sam:` | `[0:05:23] Sam: text` |
| `Speaker N:` | `[0:05:23] Speaker 1: text` |
| `FirstName-LastName:` | `[0:05:23] Jordan-Lee: text` |
| `voice-NNN:` | `[0:05:23] voice-001: text` |
| `SPEAKER_NN:` | `[0:05:23] SPEAKER_00: text` |
| `Unknown:` | `[0:05:23] Unknown: text` |

## Resolution rules

- `Sam` → `[[Sam-Rivera]]`
- A **stated name in any format** (a `FirstName-LastName` label, an `@mention`, a `First.Last` / email-style label, or a name spoken in a self-introduction) → match against `resolved_attendees` wikilinks first. If no attendee matches, a **single** `entity-registry.json` read is allowed ONLY to normalize that stated name's format. Unambiguous match → use it; ambiguous (several people share it) or absent → leave the name as written, don't guess.
- `voice-NNN` → if `manifest.transcripts[i].speakers_map["voice-NNN"]` exists, resolve to display name and wikilink-match against `resolved_attendees` first, then registry. If unmapped, keep as-is.
- `SPEAKER_NN`, `Unknown`, `Speaker N` → see "Unmapped speaker resolution" below.

## Unmapped speaker resolution

**1on1 meetings**: a 1on1 transcript has exactly one non-Sam attendee, so any `SPEAKER_NN` / `Unknown` / `voice-NNN` label that is not Sam belongs to that attendee. Attribute all such lines to the non-Sam attendee's `FirstName-LastName`. If `resolved_attendees` does not name the attendee, fall back to a paraphrase without a name.

**Multi-person meetings**: the upstream script doesn't touch these (too ambiguous to collapse deterministically). Apply introduction-based matching:

1. Scan the transcript body for self-introductions tied to a SPEAKER_NN label. Patterns: `"I'm <Name>"`, `"I am <Name>"`, `"My name is <Name>"`, `"This is <Name>"`. Also direct address from another speaker: `"<Name>, thanks for joining"`, `"over to you, <Name>"`.
2. If a SPEAKER_NN label has exactly one such match against a `resolved_attendees` first name, treat all turns by that SPEAKER_NN label as that attendee. Use the attendee's full wikilinked name (`[[FirstName-LastName]]`) when paraphrasing them in `## Discussion` / `## Decisions` / `## Actions`.
3. If multiple attendees share a first name or no match is found, leave that SPEAKER_NN unresolved and attribute by paraphrase rather than name in the body ("one of the partner-side speakers asked …").
4. Do NOT guess between attendees when evidence is ambiguous. Plaud's "Speaker N" labels are equally fine, and a wrong attribution is worse than no attribution.

**When `resolved_attendees` is empty or sparse** (Plaud transcripts often carry no attendee header): you have *less* to match against, not more licence to guess. Resolve a speaker ONLY if the transcript itself names them, a self-introduction (step 1 above) or another speaker addressing them by name, and only then via the format-normalization rule. Every speaker who is never named in the body keeps their generic `Speaker N` label, or is paraphrased without a name ("one of the device-team speakers noted…"). This empty-attendees case is the single most expensive failure mode in the pipeline: it can turn a 4-minute note into a ~9-minute registry-and-people-file hunt that still only produces low-confidence guesses. Don't.

## What you may NOT do (hard stops)

- Infer a speaker's identity from biographical details, role, expertise, accent, or language.
- Attribute a turn to someone because they "usually attend" this meeting or are on the typical roster.
- Read individual `04-People/*.md` files, or scan/grep the registry, to discover who an anonymous speaker is. (The one allowed registry read is to normalize the format of a name that is already stated, never to find an unstated one.)
- Guess between two attendees who share a first name.

Default in every ambiguous case: keep the generic label, or paraphrase the point without a name. Anonymous is a valid, final answer.

## Body entity matching (for paraphrasing into Discussion/Decisions/Actions)

Convert speaker names and @mentions to `[[wikilinks]]`. **Before resolving a bare first name against the registry, scan this meeting's `attendees:` (also `person:` for 1on1s) for a wikilink whose slug starts with that first name.** If exactly one attendee matches, use that wikilink: they're verifiably in the room. If multiple share the first name, leave the mention unresolved (don't guess between attendees). Only fall back to registry fuzzy lookup if no attendee matches.

Example: meeting `attendees: [[Sam-Rivera]], [[Vikram-Rao]], [[Bruno-Silva]]`; transcript says "Vikram suggested we extend Q2" → resolve to `[[Vikram-Rao]]` (only Vikram present), not the other two Vikrams in the registry.

## Canonical name spellings

Whisper often mishears names and proper nouns: a surname can come back as an unrelated word, a company name as a common adjective. When paraphrasing into `## Discussion` / `## Decisions` / `## Actions`, always use the spelling from `resolved_attendees` (or the registry, for company / product names). Treat the verbatim transcript text as a source of meaning, not a source of orthography. This is the single biggest quality gap left between structured transcripts and Plaud once speakers are normalized. Whisper's acoustic model is weaker on European names, but the resolved-attendee list is authoritative.
