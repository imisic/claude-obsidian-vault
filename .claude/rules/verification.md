## Verification and Anti-Fabrication

Shared rule for every skill and agent that resolves entities, extracts actions or decisions, or synthesizes notes, briefings, reviews, and prep. Referenced by `entity-matching.md`, the processor skills (email, doc, transcript), and the synthesis skills (review, 1on1-prep, project-status). The forked processor agents are told not to read rules at runtime, so the relevant guard is inlined in each of their SKILL.md files; this file is the canonical statement and the home to update.

### Three hard rules

1. **Verify, do not trust "looks done".** Prefer a mechanical check (a registry lookup, a field-present test, a date comparison, a grep) over your own impression that something is complete or correct. A check cannot be hallucinated; an impression can. This is the rule behind "read `entity-registry.json` before matching" and "use `open-actions.json`, never daily-note plain text, for completion state".

2. **No fabrication.** Never invent a person, project, date, number, decision, action, or relationship that is not actually in the source. If a value is unknown, mark it `TBD` or omit the field. An empty `## Decisions` section is correct when nothing was decided; a blank `summary` is better than an invented one. Do not round a vague statement up into a hard fact.

3. **No false absence.** Never assert that a note, person, project, or action does not exist (or that something did not happen) without searching first. "No prior 1on1", "no open actions", "nothing on this project" must come from an actual lookup (the pre-built index, a Glob, a Grep), not from memory or one lucky query. Concluding "missing" after checking a single name is the most common observed failure, more common than fabrication. Search by every plausible name, alias, and folder before concluding something is absent.

### Applying it

- **Entity matching**: when no confident match exists, return NO MATCH and let the stub pipeline handle it. Do not guess a `[[FirstName-LastName]]` for a name you cannot place, and do not pick between two people who share a first name. See `entity-matching.md`.
- **Extraction (emails, transcripts, docs)**: extract only actions and decisions that are explicitly stated. When unsure whether something is an action or a decision, leave it as prose, not a checkbox or a `## Decisions` line.
- **Synthesis (reviews, prep, status)**: every claim traces to a note you read or an index entry. If the data does not support a section, shorten or omit it rather than pad with plausible-sounding filler. When a source was unavailable (note not found, external repo absent), say what you could not check instead of guessing.
