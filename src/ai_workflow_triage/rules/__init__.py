"""Deterministic keyword extraction and the ordered rule engine.

* ``keywords.extract_signals`` — re-derives security / billing / outage / … signals
  straight from the raw text, independent of the model.
* ``table`` — the rules (R01..R14) as small pure functions in one ordered list,
  plus R99 (the default-route step the engine applies last).
* ``engine.run_rules`` — walks the list in order: a terminal outcome short-circuits;
  non-terminal outcomes accumulate.
"""
