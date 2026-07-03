
# Architecture Presentation Summary and Finder

This file is a companion guide for `ARCHITECTURE_PRESENTATION.html`.

Use it to find information quickly, understand how each service is tracked, and know which
metrics the tracker should produce.

## Fast Navigation

Open `ARCHITECTURE_PRESENTATION.html` in a browser. Use the Agenda slide buttons, arrow
keys, or browser search for the section labels below.

| Need | Search / section label | What is covered |
|---|---|---|
| Short leadership story | `Executive Path` | 10-slide executive narrative |
| Design rationale | `Conception Method`, `Design Inputs`, `Design Decision Matrix` | how the architecture was conceived, the constraints, and why each major choice was made |
| Concept purposes | `Concept Purpose Map` | purpose of each core concept and boundary concept |
| Why this matters | `Business Value` | enterprise value, risk reduction, cost visibility |
| High-level architecture | `Executive Architecture`, `System Context` | observe, normalize, preserve, derive, report |
| Core model | `Domain Model`, `TokenQuantity Schema`, `TokenEvent Schema` | trace/span/event/quantity contracts |
| Counting rules | `Counting Contract`, `Additivity`, `Counting Rules Deep Dive`, `Quantity Inclusion Rule`, `Event Inclusion Rule` | contribution formulas, inclusion gates, event authority, reconciliation, retry, streaming, and dashboard counting rules |
| Adapter creation | `Adapter Boundary`, `Adapter Creation Process`, `Adapter Inputs`, `Adapter Field Decisions`, `Adapter Output Contract`, `Adapter Test Purpose` | how adapters are designed, what evidence they use, what decisions they make, and how their semantics are tested |
| Implementation plan | `Implementation Plan`, `Implementation Phase` | phase-by-phase build plan |
| Current vs target | `Reality Check`, `Current Capability Map` | implemented, needs verification, target, future |
| Concrete example | `Concrete Walkthrough`, `Adapter Output`, `Normalized Event` | payload to adapter to event to export |
| Service tracking | `Service Tracking Overview` | how each service type is tracked |
| Metrics | `Metrics Rationale`, `Metric Creation Process`, `Metric Purpose Matrix`, `Why These Metrics`, `Metrics Catalog` | why each metric exists, how metrics are created, and formulas for usage, quality, workflow, operations, and privacy metrics |
| Operations | `Operational Runbook`, `Troubleshooting` | commands and debugging |
| Operating manual | `Operating Manual`, `Provider Example OpenAI`, `Adapter Interface Contract`, `Metric Dictionary Usage`, `Alert Thresholds`, `Playbook Provider Mismatch`, `Governance Workflow`, `Implementation Backlog` | concrete examples, interfaces, metric dictionary, thresholds, playbooks, security/privacy, deployment, testing, and backlog |
| Risks | `Risk Register`, `Known Limitations`, `Open Decisions` | limitations, decisions, governance |
| API usage | `API Example` | direct tracking, streaming, collector, export |
| Data lifecycle | `Data Lifecycle`, `Retention and Deletion` | creation, storage, collector, export, retention |

## One-Sentence Architecture

The tracker is a trace-aware token observability pipeline: it observes provider usage,
normalizes provider-specific payloads into neutral source facts, stores only those facts,
and derives contribution-safe totals later.

## Purpose of Core Concepts

| Concept | Purpose |
|---|---|
| Trace | Groups all events for one business workflow or run. |
| Span | Locates usage inside a workflow step such as retrieval, planning, generation, tool call, or export. |
| TokenEvent | Auditable unit for one provider/service observation. |
| TokenQuantity | One token number plus its type, precision, source, and additivity. |
| Adapter | Provider-specific translation layer from raw payload to neutral token facts. |
| Normalizer | Event assembly layer that combines adapter facts, trace context, observation metadata, and quality flags. |
| Additivity table | Rules that decide which fields contribute, which are subtotals, and which are unverified. |
| Repository | Stores source facts so derived reports can be recomputed. |
| Collector | Delivers telemetry reliably from services to storage/aggregation. |
| Dashboard/export | Presents derived views using safe contribution columns and quality context. |

## Adapter Creation Summary

Adapters are created from provider documentation, real response fixtures, stream terminal
payloads, error cases, and drift cases. For each provider field, the adapter decides whether
the field is a token quantity, provider total, non-token metric, or unknown token-like field.
It then assigns token type, precision, source, additivity, and subtotal parent where needed.

Adapter tests should prove semantics, not just parsing: happy path, subtotal exclusion,
missing usage, unknown fields, reconciliation, and streaming final usage.

## Operating Manual Finder

| Need | Search / section label | What is covered |
|---|---|---|
| Full lifecycle | `Operating Manual` | request to trace to adapter to event to storage to dashboard to audit |
| Provider examples | `Provider Example OpenAI`, `Provider Example Anthropic`, `Provider Example Gemini`, `Provider Example Embedding Rerank` | concrete-looking payloads and mapping decisions |
| Implementation contracts | `Adapter Interface Contract`, `Normalizer Interface Contract`, `Repository Collector Rollup Contracts` | expected interfaces and component responsibilities |
| Metric dictionary | `Metric Dictionary Usage`, `Metric Dictionary Quality`, `Metric Dictionary Workflow`, `Metric Dictionary Operations` | formulas, grains, thresholds, and owner actions |
| Alerts | `Alert Thresholds` | starting warning/critical thresholds and first responders |
| Playbooks | `Playbook Provider Mismatch`, `Playbook Unknown Usage Spike`, `Playbook Collector Offline`, `Playbook Duplicate Events` | what to do when key signals fail |
| Enterprise controls | `Security Model`, `Privacy Threat Model`, `Deployment Topologies` | auth, RBAC, privacy controls, and deployment options |
| Scale and quality | `Performance and Scaling`, `Testing Pyramid`, `Governance Workflow` | scaling triggers, test coverage, and approval workflow |
| Backlog | `Implementation Backlog` | prioritized work and acceptance criteria |

## How We Track Each Service Type

| Service type | How we track it | Main event source | Main metrics |
|---|---|---|---|
| Direct model service | Wrap provider response with `track_response(...)` or `normalize(...)` | final provider response usage | input, output, cache/reasoning breakdowns, contribution total, mismatch |
| HTTP microservice | Inject/extract `X-TokenTracker-*` headers | downstream model events under same trace | tokens by service, span, workflow, propagation loss |
| Streaming service | Use stream tracker / `consume_stream(...)` | final stream usage or fallback estimate | exact output, estimated output, interruption rate, timeout rate |
| RAG service | Create spans for retrieval, embedding, rerank, prompt, generation | embedding, rerank, and generation provider events | tokens by RAG phase, context-to-answer ratio |
| Agent service | Create hierarchical spans for planner, tools, sub-agents, final answer | model calls inside each agent step | tokens by agent phase, depth, tool-triggered usage |
| Embedding service | Adapter maps provider token count to `embedding` | embedding API response/header usage | embedding tokens, model, provider, items embedded |
| Rerank service | Adapter maps token-reporting rerank usage to `rerank_input` | rerank API usage | rerank input tokens, candidate count |
| Proxy-tracked service | Route existing tool through local proxy | proxied provider response/stream | exact usage, estimate delta, latency, status |
| Codex workflow | Import local Codex `token_count` logs | local token_count lines | Codex event totals, live budget, per-run summary |
| Collector service | Buffer and flush serialized events | token events produced elsewhere | pending, sent, dropped, retries, acks |
| Storage/export service | Read stored source facts and materialize derived views | JSONL or trace snapshots | event count, trace totals, exactness, export warnings |

## Metric Categories

Metrics are chosen only when they answer a decision. Each metric needs a question, grain,
safe formula, quality context, and audience.

### Core Usage Metrics

| Metric | Formula / source | Notes |
|---|---|---|
| `quantity_in_total` | quantity if `total_contributing` and known, else 0 | safe quantity-grain sum |
| `event_contributing_tokens` | 0 if superseded or non-authoritative, else sum `quantity_in_total` | safe event-grain sum |
| trace total | sum `event_contributing_tokens` across trace events | workflow total |
| tokens by provider | group event contributions by provider | provider comparison |
| tokens by model | group event contributions by model | model usage analysis |
| tokens by workflow | group event contributions by workflow label | business usage view |
| tokens by span type | join events to spans and group by span type | RAG/agent phase analysis |

## Detailed Counting Rules

| Rule | Formula / principle | Why it exists |
|---|---|---|
| quantity inclusion | count only known, sufficiently precise, total-contributing quantities | prevents subtotal, duplicate, unknown, and unverified fields from inflating totals |
| event inclusion | count an event only if it is authoritative, complete/accepted, and not superseded | prevents retries, dry runs, failed calls, and partial streams from becoming workflow usage |
| provider reconciliation | compare provider total to normalized contribution sum at event grain | validates adapter semantics without using provider totals as rollups |
| retry handling | superseded attempts contribute zero; the winning attempt contributes | avoids double-counting failed or retried calls |
| streaming handling | provisional stream estimates are separate from terminal exact usage | keeps exact totals and estimates from being mixed silently |
| trace/service rollup | group `event_contributing_tokens` by trace, span, service, workflow, provider, model, or day | makes dashboards additive and explainable |
| dashboard basis | every metric must declare whether it is exact, estimated, unknown, excluded, or reconciliation-only | prevents one blended number from hiding data quality |

### Quality and Reconciliation Metrics

| Metric | Meaning |
|---|---|
| `event_total_mismatch` | provider total minus sum of contribution-aware quantities |
| `coverage_ratio` | share of events with provider total available |
| `exactness_ratio` | exact quantities divided by known quantities |
| `unknown_quantity_count` | quantities with unknown precision or missing count |
| `unverified_additivity_count` | quantities/events excluded because additivity is not proven |
| `flagged_event_count` | events carrying data-quality flags |

### Workflow Metrics

| Workflow | Useful metrics |
|---|---|
| RAG | embedding tokens, rerank tokens, generation tokens, context-to-answer ratio |
| Agent | planner tokens, tool-triggered model tokens, sub-agent tokens, final answer tokens |
| Prompt suite | tokens per prompt, complete/incomplete count, estimate/provider ratio, quality pass/fail |
| Streaming | interruption rate, timeout rate, exact final usage rate, estimated output tokens |

### Operational Metrics

| Component | Useful metrics |
|---|---|
| Collector client | pending buffer size, dropped events, sent events, retry count, timeout count |
| Collector API | accepted events, malformed events, storage write failures, health status |
| Repository | append success/failure, duplicate ids, tail repair, read errors |
| Proxy | provider status, duration, time to first token, incomplete/failed events |
| Privacy audit | pass/fail, leaked prompt indicators, credential indicators |

## Correct Aggregation Rules

Safe:

```text
sum(quantity_in_total)              # quantity grain
sum(event_contributing_tokens)      # event grain
```

Unsafe:

```text
sum(quantity)
sum(provider_total_tokens)
sum(input + output + cached + reasoning)
sum(all stream events including superseded events)
```

## Important Caveats

- The deck describes intended architecture and current capabilities, not a guarantee that every
  implementation detail is bug-free.
- Simulated fixtures are useful but do not replace real payload verification.
- Provider totals are reconciliation facts, not aggregate totals.
- Live budget bars show tracker-observed usage, not account balance.
- Pricing is intentionally outside the tracker.
- Privacy depends on keeping raw prompts, completions, and credentials out of event stores.

## Best First Reading Path

1. `Executive Path`
2. `Conception Method`
3. `Design Decision Matrix`
4. `Business Value`
5. `Executive Architecture`
6. `Domain Model`
7. `Counting Contract`
8. `Service Tracking Overview`
9. `Metrics Rationale`
10. `Operating Manual`
11. `Concrete Walkthrough`
12. `Reality Check`
13. `Implementation Backlog`
14. `Known Limitations`
