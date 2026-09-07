# Security

This is a portfolio demo, not a production service. It still follows a few
deliberate rules.

## Secrets

- **No hardcoded keys anywhere.** `ANTHROPIC_API_KEY` and `TRIAGE_MODEL` are read
  from the environment (or a git-ignored `.env`) and **only** when the real
  Anthropic provider is explicitly selected. Both must be set explicitly — the
  adapter never guesses a model name.
- `.env` / `.env*` are git-ignored; `.env.example` contains blank variable names
  only.
- **Keys never enter the audit record or an error field.** `AuditRecord` is
  built from already-sanitised objects; `ValidationResult.error_detail` and every
  provider-exception string are passed through `redaction.redact_secrets()` as a
  second net (it matches common key shapes, private-key headers, and
  `NAME=secret` assignments). A test writes an audit row with a fake
  `ANTHROPIC_API_KEY` in the environment and asserts no key-shaped string appears
  in any column.
- No model chain-of-thought is requested, returned, or stored — the schema has
  no field for it.

## Secret scanning

- `scripts/scan_secrets.py` is a regex scanner run locally (`make secret-scan`)
  and in CI. It catches obvious key shapes and assignments; it does **not** catch
  high-entropy strings with no tell, or secrets split across lines.
- Before this repository was made public, a full-history scan with a dedicated
  tool (`detect-secrets` / `gitleaks`) is the gate. `scan_secrets.py` is the
  everyday check, not that gate.

## Data

- Every committed scenario, example, and test uses **invented** companies,
  people, emails, invoice numbers, and error codes (fictional "Meridian Tools").
- No real customer data, ever. Nothing is derived from any other project.

## Prompt injection

The inbound message is treated as data, never as instructions. The system prompt
says so explicitly, and — more importantly — the deterministic keyword rules and
the confidence / conflict gates still fire even if the model is fooled. This is a
first-class synthetic scenario (`security-prompt-injection`).

## Web UI

- Read-mostly: the only state change any route makes is appending one audit row.
- Every response carries `Content-Security-Policy: default-src 'self'`,
  `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`.
- No JavaScript. No external resources — the one stylesheet is served
  same-origin.
- The provider is always the deterministic mock; the pages say so on every view.

## Reporting

This is a demo repository with no users. If you spot a security-relevant issue in
the code, open a GitHub issue describing the class of problem (not a working
exploit).
