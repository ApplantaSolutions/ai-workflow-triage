"""ai-workflow-triage — an LLM interprets a message; deterministic code decides.

The package layers, in order:

1. ``models``          — the schemas (strict AI-output schema, final decision).
2. ``config``          — thresholds and heuristic values, all overridable.
3. ``rules.keywords``  — deterministic signal extraction from the raw text.
4. ``rules.table``     — the ordered rule list (R01..R14, R99).
5. ``rules.engine``    — ordered evaluation, accumulation, terminal short-circuit.
6. ``decision``        — the finalizer and the ``decision_confidence`` heuristic.

Checkpoint 2 builds layers 1-6 only. The provider, the pipeline, the audit
store, the CLI and the web UI arrive in later checkpoints.
"""

__version__ = "1.0.0"

# Bumped when the persisted audit / decision shape changes in a backward-
# incompatible way. Consumed by the audit store (a later checkpoint).
SCHEMA_VERSION = 1
