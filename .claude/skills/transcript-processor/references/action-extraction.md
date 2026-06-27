# Action item extraction: full ruleset

Load this when extracting `## Actions` from a transcript. The core rule is the **Sam-relevance test**: only emit a checkbox if Sam owns it, Sam delegated it, or someone committed to deliver it TO Sam.

## Ownership patterns

| Pattern | Format |
|---|---|
| Sam said he would do something | `- [ ] [[Sam-Rivera]] description` |
| Sam directly asked someone in the meeting | `- [ ] [[Person]] description [delegated-by:: [[Sam-Rivera]]]` |
| Someone committed to deliver TO Sam | `- [ ] [[Person]] description` |
| 1on1 / coaching, Sam instructs or coaches the other person | `- [ ] [[Person]] description [delegated-by:: [[Sam-Rivera]]]` (always: 1on1 coaching = implicit delegation) |

**SKIP**: tasks assigned by non-Sam attendees, tasks between third parties discussed in the meeting, group action items where Sam wasn't the one asking. When in doubt, SKIP.

## Anti-examples (DO NOT extract these)

From a 9-person New Ventures weekly where Sam is being introduced (no boss-chain in the room):
- Chris commits to SMB dependency math → SKIP (Chris's project, Sam is an observer)
- Raj to follow up on the device integration after the pilot → SKIP (Raj's task)
- Sofia to invite Mia and Elena → SKIP (Sofia's logistics)

From a stand-up where Sam listens to status updates:
- "I'll finish the API spec by Wednesday" said by someone-not-Sam → SKIP unless Sam explicitly asked

## DO-extract examples

From a 1on1 with Jordan (boss-chain):
- Jordan: "I'll grant you SharePoint access" → KEEP (boss commitment to Sam)
- Sam: "I'll dig up the regional rollup deck" → KEEP (Sam's own task)
- Sam to Jordan: "Can you intro me at Tuesday's team weekly?" → KEEP, owner=Jordan, `[delegated-by:: [[Sam-Rivera]]]`

From a small 3-person sync where Sam is asking:
- Sam: "Mia, can you propose deep-dive slots this week?" → KEEP, owner=Mia, `[delegated-by:: [[Sam-Rivera]]]`

**Heuristic**: would Sam miss this task if it disappeared? If no, SKIP.

## Forgettability test (apply BEFORE emitting `- [ ]`)

A task earns a checkbox only if its description carries at least one of:

- **Explicit time horizon**: "by Friday", "before May 19", "this week", a weekday name, an ISO date
- **Deliverable noun**: deck, doc, draft, list, intro, decision, approval, proposal, plan, analysis, summary
- **Small-ask verb**: send, share, forward, ping, ask, check, confirm, dig up, find, follow up, schedule, set up, organize
- **Explicit blocker**: "waiting on", "once X confirms"

If none match, the item is a conversational artifact, not a trackable task. Surface it as a bullet under `## Discussion`, not under `## Actions`.

**DO NOT emit as `- [ ]`:**
- "Sam to read PRD": no horizon, no deliverable, no ask
- "Sam to attend May 19 workshop": already a calendar event
- "Sam to continue handover support": ongoing background, not discrete
- "Sam to help manage pushback on X": stance, not action
- "We will revisit this next week": group hand-wave, not Sam-owned
- "Sam to drive communication and steering with the engineering team": vague mandate / ongoing responsibility, no discrete deliverable
- "Sam to explore upsell mechanics with partner colleagues": open-ended exploration, no horizon or deliverable
- "Mia to book the recurring Wednesday 1on1": routine logistics that will happen anyway; not a tracked task
- "Sam to drive/own/steer X": any "drive/own/steer/manage" verb without a concrete deliverable is a stance, not an action

**DO emit:**
- "Sam to read deck before Thursday committee": `before` + `deck`
- "Sam to dig up old NDA and forward to Jordan": `dig up` + `forward`
- "Sam to schedule 1on1 with new team member": `schedule`
- "Jordan to grant SharePoint access": owed-to-Sam from boss-chain

Heuristic: if Sam listens to this transcript in 3 days, will he remember it wasn't done? If yes → KEEP. If it'll have happened anyway → SKIP.

`write-notes.py` will demote forgettability failures to plain bullets with `[demoted:: forgettability]`. Skipping at emit time keeps your output clean and saves tokens.

## VIP hygiene

`write-notes.py` runs a deterministic hygiene pass after you finish. Large-meeting noise will be auto-stripped to plain bullets, but VIP-attended meetings (boss-chain, stakeholder) skip that filter entirely, so be especially disciplined there: any checkbox you emit will survive.
