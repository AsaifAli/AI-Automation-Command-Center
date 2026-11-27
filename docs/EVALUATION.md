# Evaluation Plan

## Philosophy

AI quality should be measured per workflow. A single generic LLM score hides important failure modes.

## Current executable gates

`evals/cases.json` is intentionally small and deterministic so CI can run it without an external provider.

| Workflow | Gate |
|---|---|
| Content | correct number of channel outputs |
| Competitor | actionable recommendation list exists |
| Outreach | every candidate requires approval |
| KPI | blocker is detected as risk |

Run:

```bash
python scripts/evaluate.py
```

## Production quality dimensions

### Content

- relevance
- factuality
- tone adherence
- channel fit
- human acceptance rate
- edit distance from approved copy

### Competitor intelligence

- source freshness
- primary-source ratio
- duplicate rate
- high-signal precision
- analyst acceptance rate
- time saved per briefing

### Outreach

- qualification precision
- personalization quality
- policy violation rate
- approval rate
- reply rate
- positive response rate

### KPI

- field extraction accuracy
- normalization accuracy
- blocker/risk precision and recall
- missing-data detection
- briefing acceptance rate

## Operational metrics

- p50/p95 workflow latency
- provider error rate
- retry rate
- token usage
- estimated model cost
- workflow completion rate
- approval rate
- downstream outcome

## Regression strategy

Maintain a versioned evaluation set. Run it when any of the following changes:

- prompts
- models
- retrieval/source ranking
- agent policies
- output schemas

A production deployment should block promotion when critical quality metrics regress beyond agreed thresholds.

## Shared Engineering Assurance Harness

FlowPilot can report privacy-safe workflow activity to the studio's evaluation harness. Configure these server-side variables on the FlowPilot API service:

- `EVALUATION_HARNESS_URL=https://ai-engineering-evaluation-harness.onrender.com`
- `EVALUATION_HARNESS_INGEST_TOKEN` — a unique FlowPilot-only token.

The evaluation-harness service must include the same token under the `flowpilot` key in `HARNESS_INGEST_TOKENS_JSON`. Keep it separate from EvidenceFlow and QuoteSense tokens.

Only run ID, workflow name, status, elapsed time, model label, token counts, approval-pending state and warning count are sent. Prompts, user inputs, generated artifacts, evidence details, model responses and provider credentials are never included. Delivery is best-effort with a short timeout; an unavailable harness never changes or blocks the workflow result.

The harness's synthetic FlowPilot replay pack remains a separate quality-gate suite. Runtime activity counters are not represented as synthetic evaluation scores.
