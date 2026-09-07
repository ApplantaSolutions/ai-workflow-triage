# ADR-0001 — The LLM interprets; deterministic code decides

**Status:** accepted (CP1)

## Context

The system routes messy inbound messages to teams and decides urgency,
escalation, and whether a human must look first. These are consequential
operational decisions. An LLM reads messy text well but is inconsistent, can be
confidently wrong, can be steered by the text it is reading (prompt injection),
and can emit malformed output.

## Decision

The LLM produces only a `TriageInterpretation` — a set of **observations**
(category *suggestion*, urgency *read*, summary, extracted entities, risk
signals, missing info, self-reported confidence, a short rationale). The words
`route`, `escalation`, and `human_review` are not in its schema.

A deterministic, ordered rule engine consumes the validated interpretation
**plus signals it re-derives from the raw text itself** and produces the
`TriageDecision`. If a signal can be detected deterministically (a "charged
twice" phrase, security language, a confidence below threshold, malformed
output), the model is not asked to decide the consequence.

## Consequences

- A broken or hostile model response cannot produce a confident route — it
  deterministically becomes `HUMAN_REVIEW`.
- The keyword layer is an independent check on the model; the two can disagree,
  and disagreement is itself a routing signal (`R11`).
- The system is fully testable without an LLM.
- Cost: the deterministic rules need maintenance and can be over-eager
  (mitigated by biasing toward over-triage — a false "security" is a nuisance, a
  false negative is not).
- This is the same principle as `rubric-eval-harness` (deterministic checks vs.
  model judgment) applied to an operational workflow instead of an evaluation.
