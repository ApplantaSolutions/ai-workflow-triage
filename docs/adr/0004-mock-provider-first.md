# ADR-0004 — Mock-provider-first; the offline demo needs no API key

**Status:** accepted (CP1)

## Context

The project must be useful — runnable, testable, demonstrable — with no API key
and no network. The real model integration should be optional and deferred.

## Decision

- `Provider` is a small protocol: `classify(text) -> ProviderResult`, raises
  `ProviderError`.
- `MockProvider` is the default. It returns deterministic canned responses keyed
  by `scenario_id` (from `data/synthetic_scenarios.json`), including deliberately
  malformed responses and an error mode. For free-text input with no scenario it
  returns a deterministic generic interpretation (`category=OTHER`,
  `model_confidence≈0.5`) — which correctly tends to route to `HUMAN_REVIEW`.
- `AnthropicProvider` reads `ANTHROPIC_API_KEY` + `TRIAGE_MODEL` from the
  environment only, and is **never** called by the test suite or the default
  demo. Wiring it in is a `--provider anthropic` flag, added in CP3 and left
  unexercised until a separately-authorised smoke test.
- The whole test suite runs offline. `scripts/build_examples.py` regenerates
  committed audit examples deterministically (fixed clock); a test byte-compares
  them.

## Consequences

- A recruiter can `pip install -e .` and see real output in 30 seconds.
- CI is fast, free, and deterministic.
- The claim "runs against a real model" is **not** made in V1 — the README says
  the pipeline is implemented and mock-tested, with the real adapter deferred by
  design. (Same honest position as `rubric-eval-harness`.)
