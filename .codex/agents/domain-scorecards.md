# Domain Excellence Scorecards

Use these scorecards to rate whether the tracker is just functional or genuinely strong.
Score each domain from 1 to 5.

## Level Definitions

- 1: works on happy path only
- 2: handles obvious errors
- 3: has regression tests for core invariants
- 4: has adversarial tests, audit outputs, and clear failure signals
- 5: hard to misuse, hard to misreport, and easy to audit under real traffic

## Core Accounting

Level 5 means:

- storage and derived fields are impossible to confuse in normal APIs
- overlap and trust axes represent every required cell
- authority is typed and fail-closed when explicitly false
- supersession removes contribution without hiding history
- totals expose floor, estimate, ceiling, and reasons for uncertainty

## Provider Surfaces

Level 5 means:

- every provider/surface has a declared usage contract
- real payload fixtures lock provider-specific behavior
- unknown or changed fields fail closed and are flagged
- deployment, model, region, and provider request IDs are preserved separately
- cache/reasoning/tool/embedding quantities reconcile without double counting

## Trace And Streaming

Level 5 means:

- async and thread context propagation are tested under parallel load
- partial streams are visible as lower-bound or estimate data
- final usage supersedes stream estimates by request correlation id
- retries, truncation, content filters, and timeouts have distinct signals
- trace loss is measurable, not anecdotal

## Storage And Collector

Level 5 means:

- append paths are idempotent and concurrency-safe
- invalid inputs are counted and optionally dead-lettered
- old bad rows do not make the store unreadable by default
- iterators support high-volume analytics
- secrets and raw prompt leaks are scanned before release

## Analytics And Export

Level 5 means:

- every export agrees with the in-memory accounting model
- mismatch metrics include direction and magnitude
- completeness and exactness are first-class measures
- Power BI cannot accidentally sum an unsafe field without warnings
- audit bundles explain "what is the number and why might it be wrong"

## Ops And Release

Level 5 means:

- offline CI proves deterministic behavior
- live smoke tests produce redacted audit bundles
- Foundry and Azure classic profiles are unambiguous
- doctor catches storage, env, network, and secret posture issues
- docs teach the exact operational path without relying on shell memory

