# ADR-0005 — HUMAN_REVIEW / NEEDS_INFORMATION supersede auto-routing; SECURITY is the exception

**Status:** accepted (CP1)

## Context

Several rules can fire on one request. A routing rule may pick a team while a
gate rule decides the request is too uncertain or too incomplete to auto-route.
We need one clear, testable finalisation rule.

## Decision

After the ordered rule list runs, the finalizer resolves the disposition:

1. If any rule set `disposition = HUMAN_REVIEW` (or `human_review = true`) →
   final `route = HUMAN_REVIEW`, and the route the request *would* have taken is
   kept as `suggested_route`.
2. Else if any rule set `disposition = NEEDS_INFORMATION` → final `route =
   NEEDS_INFORMATION`, `suggested_route` kept.
3. Else `disposition = ROUTED` with the accumulated route (or `SUPPORT_TIER1`
   from `R99` if none was set).

**Exception:** a **terminal** SECURITY outcome (`R02`) is never downgraded. It
routes to `SECURITY` and escalates to `CRITICAL` even though a human will also
see it — the SECURITY queue is human-staffed, so the safety property (a person
looks at it) holds, and routing it there is more useful than a generic
`HUMAN_REVIEW`.

Rationale for the general precedence: a human/info gate exists precisely because
auto-routing would be unsafe or premature here; it should win. The `suggested_route`
field means no information is lost — a reviewer sees what the system *would* have
done and can approve it in one step.

## Consequences

- One place (`decision.py` finalizer), fully unit-tested, decides this.
- The audit always records both the final route and the suggested route.
- A reviewer's job is usually "confirm or correct the suggested route", not
  "start from scratch".
