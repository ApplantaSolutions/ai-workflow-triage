# ADR-0002 — Rules are an ordered, readable table, not one function

**Status:** accepted (CP1)

## Context

Routing logic tends to collapse into a single large `if/elif` function that is
hard to read, hard to test, and hard to reason about for precedence.

## Decision

Each rule is a small pure function `rule(ctx: RuleContext) -> RuleOutcome | None`
with a stable `rule_id`, living next to one entry in an **ordered list** in
`src/ai_workflow_triage/rules/table.py`. The engine (`rules/engine.py`) walks the list in
order:

- a rule returning `None` did not apply (recorded as `applied: false` in the
  trace);
- a non-terminal `RuleOutcome` **accumulates** (first routing rule wins the
  route; urgency floor takes the max; `human_review` is sticky; reasons append);
- a `terminal` `RuleOutcome` stops evaluation (hard override — `R01`, `R02`).

Precedence is therefore just **list order + the terminal flag**, both visible in
one file. Every rule is unit-tested in isolation (fires / doesn't fire) and the
important orderings are tested in `test_rules_precedence.py`.

## Consequences

- Adding, removing, or reordering a rule is a localized, reviewable change.
- The `rule_trace` in the audit shows *every* rule and whether it applied —
  a reader can see exactly why the decision came out the way it did.
- Cost: a tiny bit more boilerplate than one function. Worth it.
