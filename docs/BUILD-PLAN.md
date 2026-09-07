# ai-workflow-triage — Build Plan

Small checkpoints. Each ends with a **STOP + review gate**: report PASS /
CONDITIONAL PASS / FAIL and wait for explicit JR + ChatGPT authorization before
the next one. No auto-advance.

Locked context (do not reopen without a discovered contradiction or a
JR + ChatGPT decision):
- the LLM interprets; the deterministic layer authorizes the disposition
  (ADR-0001);
- rules are an ordered readable table, not one function (ADR-0002);
- SQLite via stdlib for the audit (ADR-0003);
- mock-provider-first, offline demo works with no key (ADR-0004);
- `HUMAN_REVIEW` / `NEEDS_INFORMATION` supersede auto-routing, except a terminal
  SECURITY route (ADR-0005);
- no other project is touched; all data synthetic; secrets env-only.

---

## CP1 — Architecture & specification ✅ (this checkpoint)

`docs/PROJECT-SPEC.md`, `docs/TEST-PLAN.md`, `docs/SYNTHETIC-SCENARIOS.md`,
`docs/BUILD-PLAN.md`, `docs/adr/0001..0005`. No code, no git.

**Gate:** JR + ChatGPT approve the schema, the rule table, the precedence, and
the scenario matrix.

---

## CP2 — Models + deterministic rule engine (the trustworthy core)

Build:
- `src/ai_workflow_triage/__init__.py` (`__version__`, `SCHEMA_VERSION`)
- `src/ai_workflow_triage/models.py` — enums (`Category`, `Urgency`, `RiskSignal`, `Route`,
  `EscalationLevel`), `TriageRequest`, `TriageInterpretation` (strict,
  `extra="forbid"`), `ValidationResult`, `KeywordSignals`, `TriageDecision`,
  `Reason`, `RuleTraceEntry`
- `src/ai_workflow_triage/config.py` — thresholds (`min_confidence=0.55`), toggles
  (`escalation_forces_review=true`), paths
- `src/ai_workflow_triage/rules/keywords.py` — the deterministic signal extractor + the
  regex/keyword config, each family documented
- `src/ai_workflow_triage/rules/table.py` — rules `R01`–`R14`, `R99` as the ordered list
- `src/ai_workflow_triage/rules/engine.py` — ordered evaluation, accumulation, terminal
  short-circuit, `rule_trace`
- `src/ai_workflow_triage/decision.py` — the finalizer + `decision_confidence` heuristic
- `pyproject.toml`, `.gitignore`, `.gitattributes` (`* text=auto eol=lf` +
  `*.png binary` from day one), `.env.example`, `ruff` config
- `errors.py` — `TriageError`, `ConfigError`, `ProviderError`
- tests: `test_models` (schema validation folded in), `test_keywords`,
  `test_rules_individual`, `test_rules_precedence`, `test_decision`.
  `test_validation` → **CP3**, with `validation.py` (string-level parsing).

**Decisions settled in CP2:**
- `R07 NEEDS_INFORMATION` + `R10 HUMAN_REVIEW` both apply → **`HUMAN_REVIEW`
  wins**, every `NEEDS_INFORMATION` ask preserved in `missing_information`
  (locked correction 3). `R07` / `R09` alone stay `NEEDS_INFORMATION`
  (corrections 1 & 2).
- `MENTIONS_CHURN` / deadline → **urgency floor only, never escalation** (locked
  correction 4). Escalation is set only by `R02` (`CRITICAL`) and `R03`
  (`PRIORITY`).
- `R11` conflict shapes: **four explicit families only** (locked correction 5) —
  feature+CRITICAL, feature+churn, support/feature + security-language (in the
  engine this is subsumed by the terminal `R02`), billing-language +
  outage-language. No vague "looks inconsistent" rule.

**Gate:** every rule has a fires / doesn't-fire test; precedence tests green;
`decision_confidence` table fully covered; ruff + ruff-format clean; **offline,
no provider yet**. — **MET (CP2 done): 164 tests pass.**

---

## CP3 — Provider abstraction + mock provider + strict parsing

Build:
- `src/ai_workflow_triage/providers/base.py` — `Provider` protocol,
  `ProviderRequest`, `ProviderResult` (re-exports `ProviderError`)
- `src/ai_workflow_triage/providers/mock.py` — deterministic; scenario-keyed canned
  responses + a generic fallback; malformed / error modes; no network import
- `src/ai_workflow_triage/providers/anthropic.py` — real adapter, env-only config,
  lazy `anthropic` import; **not constructed, called, or network-tested**.
  `anthropic` is the `[real]` optional extra.
- `src/ai_workflow_triage/prompt.py` — classifier prompt construction
- `src/ai_workflow_triage/validation.py` — `parse_interpretation` (JSON extraction:
  bare object / fenced / one object in prose; conservative — no repair) +
  `interpret(request, provider)` (the boundary: provider → parse → **one**
  corrective retry → `ValidationResult`)
- `src/ai_workflow_triage/scenarios.py` + `data/synthetic_scenarios.json` — the 15
  scenarios *(CP3 update — the loader + packaged data; CP1 said
  `scenarios/synthetic.json`, this is the reconciled location so it ships with
  the wheel)*
- `src/ai_workflow_triage/redaction.py` — `redact_secrets`, applied to every
  `error_detail` and provider-exception string *(CP3 addition — not in the CP1
  plan)*
- tests: `test_prompt`, `test_mock_provider`, `test_validation`

**Gate:** mock deterministic; every malformed/error mode yields a
`ValidationResult` that the engine turns into `HUMAN_REVIEW`; `test_prompt`
confirms the schema keys + "don't invent facts" + injection defence. —
**MET (CP3 done): 230 tests pass; all 15 scenarios compose interpret→decide to
their expected blocks.**

---

## CP4 — Processing pipeline + audit records

**CP3 cleanup (done here):** `AnthropicProvider` no longer has a default model
name — `TRIAGE_MODEL` must be set explicitly or construction raises `ConfigError`.

Build:
- `src/ai_workflow_triage/audit.py` — `AuditRecord` (strict pydantic) + `AuditStore`
  protocol + `SqliteAuditStore` (`init_db` idempotent, `write_record`,
  `get_record`, `list_records` newest-first; one shared connection + lock;
  `:memory:` supported). `AuditRecord` does **not** carry `audit_persisted` —
  whether the write succeeded is on `ProcessResult`, not in the row it describes
  *(CP4 reconciliation — the CP1 §12 list implied it was a column)*.
- `src/ai_workflow_triage/pipeline.py` — `process_request(request, provider, *,
  config, audit_store, now, request_id) -> ProcessResult` (`request_id`,
  `decision`, `audit_record`, `audit_persisted`, `persistence_error`).
  Orchestration only — no decision logic; `decide()` stays the single source.
- `src/ai_workflow_triage/cli.py` — `triage analyze --text|--scenario [--db] [--json]`,
  `triage validate-scenarios`, `--version`. Human output splits
  "WHAT THE AI OBSERVED" from "DETERMINISTIC DECISION"; `[DETERMINISTIC]` /
  `[AI OBSERVATION]` tags on every reason.
- `scripts/build_examples.py` — every scenario through the real pipeline (fixed
  clock, deterministic ids, `:memory:` store) → `examples/audit-<id>.json`;
  `--check` mode for CI.
- `scripts/scan_secrets.py` — regex scanner (key shapes, private-key headers,
  `NAME=secret` assignments; `--` skips example/placeholder lines).
- `Makefile` — `install / test / lint / format-check / secret-scan / examples /
  examples-check / demo`.
- Small additive schema change: `ValidationResult` gained
  `provider_name` / `provider_model` (both `str | None`) so the audit can record
  "provider model if known" *(CP4 — enabled by the CP4 AuditRecord requirement;
  the CP2 engine is untouched)*.
- tests: `test_pipeline`, `test_audit`, `test_scenarios` (byte-compare of
  `examples/`), `test_cli`, `test_redaction`

**Gate:** all 15 scenarios land on their `expected` block through the full
pipeline; reproducibility byte-compare green; no key/env value in any audit
column (test asserts it with a fake key in the env); persistence failure is
non-fatal and its error is redacted. — **MET (CP4 done): 299 tests pass;
`scan_secrets` clean; `build_examples.py --check` green.**

---

## CP5–CP8 — Web UI + hardening + docs + private GitHub (consolidated pass)

> JR + ChatGPT authorized an **accelerated final completion pass** after CP4
> instead of four more micro-checkpoints. Architecture (CP1–CP4) stayed locked;
> no new product features. What was built:

**Web UI (`app.py` + `web/`):**
- `create_app(audit_store, provider)` factory; module-level `app` for uvicorn.
- `GET /` (form + scenario picker + quick-scenario buttons incl. the flagship),
  `POST /analyze` (result page), `GET /demo/{scenario_id}` (shareable read-only
  result link — does **not** write an audit row), `GET /audit` + `GET /audit/{id}`,
  `GET /health`, `POST /api/analyze`. GET-only pages 405 on POST; POST-only 405 on GET.
- `web/templates/` (`base`, `index`, `result`, `audit_list`, `audit_detail`,
  `not_found`) + **one same-origin stylesheet** `web/static/triage.css`. **No
  JavaScript. No external resources.** Result page: `panel--ai` (muted) visually
  distinct from `panel--decision` (authority border); `[DETERMINISTIC]` /
  `[AI OBSERVATION]` chips on every reason; a "did not simply follow the model"
  callout when the deterministic outcome diverges; full rule trace in `<details>`.
- Security middleware: `Content-Security-Policy: default-src 'self'`,
  `X-Frame-Options: DENY`, `nosniff`, `no-referrer` on every response.
- Every page says "offline demo · mock provider · no live LLM called".
- `[web]` optional extra (`fastapi`, `jinja2`, `uvicorn`, `python-multipart`);
  `dev` includes it plus `httpx`, `detect-secrets`.

**Hardening / CI:**
- `.github/workflows/ci.yml` — Python 3.11 / 3.12 / 3.13: install → ruff check →
  ruff format --check → pytest → `build_examples.py --check` → `scan_secrets.py`
  → `detect-secrets scan --baseline`.
- `.secrets.baseline` committed (0 findings).
- CLI reconfigures stdout to UTF-8 (`errors="replace"`) so a legacy Windows
  console does not crash on the em dash / arrows.
- Version → **1.0.0**.

**Docs:** employer-facing `README.md` (answers the 11 required questions +
screenshots in `docs/images/`), `SECURITY.md`, `CONTRIBUTING.md`, `LICENSE`
(MIT).

**Tests:** `test_app.py` (35 — happy + failure + audit visibility + health + API
+ security headers + zero-external-resources + shareable demo links).

**Gate:** result page renders the AI/deterministic split; `/audit` + `/health`
work; generated HTML has zero external resources and no JS; API matches the
pipeline; failure paths render `HUMAN_REVIEW`; CSP present on every response;
responsive at 390 px (CDP-measured: no horizontal overflow). Microsoft Edge
visual review delivered. — **MET: 322 tests; ruff + format + both secret
scanners clean; `build_examples.py --check` green.**

---

## CP6 — Testing / hardening *(folded into the consolidated pass above)*

- Full failure matrix (`TEST-PLAN.md` §3) covered.
- CI (`.github/workflows/ci.yml`): install → ruff → format-check → secret-scan →
  pytest → demo-reproducibility, on Python 3.11 / 3.12 / 3.13.
- ruff + ruff-format clean; secret scan clean; deterministic-core coverage
  complete.
- Edge hardening: oversize input, empty input, weird unicode, a scenario where
  the model returns a valid interpretation whose `category` has no rule (must not
  fall through silently).

**Gate:** green suite on all three Python versions locally; every §3 failure row
has a test.

---

## CP7 — README / screenshots / CI badge / security / publication readiness

- `README.md` (recruiter-facing, same skeleton as `rubric-eval-harness`:
  one-liner → the problem → what it demonstrates → screenshot → 30-second
  offline demo → how it works → **AI OBSERVATION vs DETERMINISTIC DECISION** →
  JR's role / AI-assisted disclosure → limitations → license)
- `docs/methodology.md` (the rule table, precedence, the `decision_confidence`
  heuristic, all limitations)
- `docs/adr/` finalised
- `CONTRIBUTING.md`, `SECURITY.md` (what `scan_secrets.py` does / does not
  guarantee; recommend gitleaks/detect-secrets for the full-history gate)
- screenshots of the result page (incl. the prompt-injection scenario — the
  flagship) into `docs/images/`
- committed `examples/` regenerated; `.gitattributes` verified
- `pyproject.toml` metadata: name, description, author name only, no invented
  URLs, MIT (pending JR confirm — consistent with `rubric-eval-harness`)

**Gate:** full offline verification; tree audit (no `.env`, no absolute paths, no
other-project references, no secrets in history); README command check.

---

## CP8 — Private GitHub review

- Security gate: two independent secret scans (`scan_secrets.py` +
  `detect-secrets` full history).
- `git init` **only** inside `C:\Users\ririg\portfolio\ai-workflow-triage`;
  verify `.gitignore` before staging; inspect the staged set; one initial commit
  with the honest AI-assisted trailer.
- Create the **PRIVATE** GitHub repo (`ApplantaSolutions/ai-workflow-triage`);
  push `main`.
- Verify: private, README renders, screenshot renders, CI run green on
  3.11/3.12/3.13.

**Gate:** JR reviews the GitHub presentation.

---

## CP9 — Public launch

- After JR's review: description + topics; flip **only** this repo to public;
  verify unauthenticated access, README, badge, LICENSE, Actions history.
- Add to the `ApplantaSolutions/ApplantaSolutions` profile README "Featured Work"
  as the second project (a real link, replacing the "in preparation" text).
- Recruiter-view check of the two-project profile.

**Gate:** public repo is clean, credible, and correctly positioned next to
`rubric-eval-harness`.
