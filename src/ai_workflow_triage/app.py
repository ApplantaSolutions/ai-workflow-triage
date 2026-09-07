"""FastAPI application factory.

Pages: ``GET /`` (form), ``POST /analyze`` (result), ``GET /audit`` +
``GET /audit/{id}`` (audit visibility), ``GET /health``. JSON: ``POST /api/analyze``.

* GET-only pages return 405 on POST (FastAPI's default for an unmatched method).
* Every response carries a strict CSP (`default-src 'self'`), `X-Frame-Options:
  DENY`, `nosniff`, `no-referrer`.
* The only state change any route makes is appending one audit row.
* The provider is always ``MockProvider`` — no network, no API key, no live LLM.
"""

from __future__ import annotations

import os
from importlib import resources
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError

from . import __version__
from .audit import AuditStore, SqliteAuditStore
from .errors import ConfigError
from .models import TriageRequest
from .pipeline import ProcessResult, process_request
from .providers import MockProvider
from .providers.base import Provider
from .scenarios import ScenarioSpec, load_scenarios

_WEB_DIR = resources.files("ai_workflow_triage.web")
_TEMPLATE_DIR = str(_WEB_DIR.joinpath("templates"))
_STATIC_DIR = str(_WEB_DIR.joinpath("static"))
_DEFAULT_DB = os.environ.get("TRIAGE_DB_PATH", "triage-audit.sqlite")
_AUDIT_PAGE_SIZE = 25


class ApiAnalyzeRequest(BaseModel):
    text: str | None = None
    channel: str | None = None
    scenario_id: str | None = None


def _scenario_options() -> list[ScenarioSpec]:
    return sorted(load_scenarios().values(), key=lambda s: s.title)


def _result_context(result: ProcessResult) -> dict[str, Any]:
    d = result.decision
    rec = result.audit_record
    interp = rec.interpretation
    diverged = interp is not None and (
        d.urgency.value != interp.urgency.value
        or d.route.value in {"SECURITY", "HUMAN_REVIEW", "NEEDS_INFORMATION"}
    )
    return {
        "result": result,
        "decision": d,
        "record": rec,
        "interpretation": interp,
        "deterministic_reasons": [r for r in d.reasons if r.source == "DETERMINISTIC"],
        "ai_reason": next((r for r in d.reasons if r.source == "AI_OBSERVATION"), None),
        "diverged": diverged,
    }


def create_app(
    *,
    audit_store: AuditStore | None = None,
    provider: Provider | None = None,
) -> FastAPI:
    store: AuditStore = audit_store or SqliteAuditStore(_DEFAULT_DB)
    active_provider: Provider = provider or MockProvider()
    templates = Jinja2Templates(directory=_TEMPLATE_DIR)
    templates.env.globals["version"] = __version__
    templates.env.globals["provider_name"] = active_provider.name

    app = FastAPI(title="ai-workflow-triage", version=__version__, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.middleware("http")
    async def _headers(request: Request, call_next):  # noqa: ANN001
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def _run(request: TriageRequest) -> ProcessResult:
        return process_request(request, active_provider, audit_store=store)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {"scenarios": _scenario_options()})

    @app.post("/analyze", response_class=HTMLResponse)
    def analyze(request: Request, text: str = Form(""), scenario_id: str = Form("")):
        scenario_id = (scenario_id or "").strip()
        try:
            if scenario_id:
                spec = load_scenarios().get(scenario_id)
                if spec is None:
                    raise ConfigError(f"unknown scenario_id: {scenario_id!r}")
                triage_request = TriageRequest(text=spec.input_text, scenario_id=spec.id)
            else:
                triage_request = TriageRequest(text=text)
        except (ValidationError, ConfigError) as exc:
            return templates.TemplateResponse(
                request,
                "index.html",
                {"scenarios": _scenario_options(), "error": _friendly_error(exc), "text": text},
                status_code=400,
            )

        result = _run(triage_request)
        return templates.TemplateResponse(request, "result.html", _result_context(result))

    @app.get("/demo/{scenario_id}", response_class=HTMLResponse)
    def demo(request: Request, scenario_id: str):
        # a shareable, bookmarkable link to one scenario's result. Read-only:
        # it does NOT write an audit row (unlike the form POST).
        spec = load_scenarios().get(scenario_id)
        if spec is None:
            return templates.TemplateResponse(
                request, "not_found.html", {"audit_id": scenario_id}, status_code=404
            )
        result = process_request(
            TriageRequest(text=spec.input_text, scenario_id=spec.id),
            active_provider,
            audit_store=None,
        )
        return templates.TemplateResponse(request, "result.html", _result_context(result))

    @app.get("/audit", response_class=HTMLResponse)
    def audit_list(request: Request):
        records = store.list_records(limit=_AUDIT_PAGE_SIZE)
        return templates.TemplateResponse(
            request, "audit_list.html", {"records": records, "page_size": _AUDIT_PAGE_SIZE}
        )

    @app.get("/audit/{audit_id}", response_class=HTMLResponse)
    def audit_detail(request: Request, audit_id: str):
        record = store.get_record(audit_id)
        if record is None:
            return templates.TemplateResponse(
                request, "not_found.html", {"audit_id": audit_id}, status_code=404
            )
        return templates.TemplateResponse(request, "audit_detail.html", {"record": record})

    @app.get("/health", response_class=JSONResponse)
    def health():
        try:
            store.list_records(limit=1)
            db_ok = True
        except Exception:  # noqa: BLE001
            db_ok = False
        return {
            "status": "ok" if db_ok else "degraded",
            "version": __version__,
            "provider": active_provider.name,
            "mode": "offline-mock",
            "db_ok": db_ok,
        }

    @app.post("/api/analyze", response_class=JSONResponse)
    def api_analyze(payload: ApiAnalyzeRequest):
        try:
            if payload.scenario_id:
                spec = load_scenarios().get(payload.scenario_id)
                if spec is None:
                    raise ConfigError(f"unknown scenario_id: {payload.scenario_id!r}")
                triage_request = TriageRequest(text=spec.input_text, scenario_id=spec.id)
            else:
                triage_request = TriageRequest(text=payload.text or "", channel=payload.channel)
        except (ValidationError, ConfigError) as exc:
            return JSONResponse({"error": _friendly_error(exc)}, status_code=400)

        result = _run(triage_request)
        return {
            "request_id": result.request_id,
            "audit_persisted": result.audit_persisted,
            "decision": result.decision.model_dump(mode="json"),
        }

    return app


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, ConfigError):
        return str(exc)
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        return str(first.get("msg", "invalid request"))
    return "invalid request"


# `uvicorn ai_workflow_triage.app:app` works, but the FastAPI instance (and its
# SQLite store) is built lazily on first attribute access — so importing
# `create_app` for tests has no side effect and creates no database file.
_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    if name == "app":
        global _app
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
