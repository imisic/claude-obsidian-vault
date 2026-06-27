---
name: Project-Alpha
status: active
products: ["[[Orion]]"]
markets: ["[[DE]]"]
segment: ["[[B2B]]"]
owner: "[[Sam-Rivera]]"
type: project
quarter: 2026-Q1
okr-link: "[[2026-Q1-New-Ventures]]"
---

# Project-Alpha

> [!example] Example project note (the hub the demo interactions, the OKR, and `/w-project-status` all link to). Fictional demo data. Replace or delete. See README → "Example content".

## What it is
The DE launch of [[Orion]], Acme's new B2B partner-integration product. Covers the [[Northwind]] fulfillment partnership, the pilot customer cohort, and the build on top of [[Nimbus]].

## Current status
**As of 2026-03-05:** [[Northwind]] terms agreed and in legal review; DE pilot build in progress; pilot customer pipeline is the main risk (see [[2026-Q1-New-Ventures]] KR2).

## Key stakeholders
- [[Sam-Rivera]] (owner)
- [[Jordan-Lee]] (sponsor)
- [[Mia-Fischer]] (business development)
- [[Raj-Patel]] (engineering)
- [[Sofia-Costa]] (design)
- [[David-Klein]] (partnerships, [[Northwind]] contract)

## Open items
- [ ] [[Sam-Rivera]] confirm DE pilot customer shortlist [due:: 2026-03-13]
- [ ] [[David-Klein]] return signed [[Northwind]] terms [due:: 2026-03-12] [delegated-by:: [[Sam-Rivera]]]

## Dependencies
- [ ] [dep:: [[Project-Beta]]] partner-sync API capacity on [[Nimbus]] [status:: open]

## Risks & Issues
- [risk:: pilot pipeline depends on closing Northwind terms] [impact:: high] [mitigation:: legal review fast-tracked, shortlist prepped in parallel]
- [risk:: Nimbus sync capacity not yet load-tested at launch volume] [impact:: medium] [mitigation:: load test scheduled with [[Arun-Shah]]]

## Decisions log
| Date | Decision | Rationale | Owner | Status |
|-|-|-|-|-|
| 2026-01-15 | Proceed to discovery | Exec sponsor secured | [[Sam-Rivera]] | confirmed |
| 2026-03-04 | DE is the launch market | Largest B2B demand, [[Northwind]] coverage | [[Sam-Rivera]] | confirmed |
| 2026-03-04 | Pilot scope capped at 5 customers | Keep support load manageable for first cohort | [[Jordan-Lee]] | confirmed |

## Meeting notes
![[project-interactions.base]]

## History
- 2026-01-15 Kickoff, discovery started
- 2026-03-04 SteerCo: market and pilot scope locked ([[2026-03-04-steerco-project-alpha]])
