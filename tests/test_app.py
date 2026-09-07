"""FastAPI UI — TEST-PLAN §2.2. Offline; no network; no live LLM."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from ai_workflow_triage.app import create_app
from ai_workflow_triage.audit import SqliteAuditStore
from ai_workflow_triage.models import TriageRequest
from ai_workflow_triage.pipeline import process_request
from ai_workflow_triage.providers import MockProvider


@pytest.fixture
def client():
    store = SqliteAuditStore(":memory:")
    app = create_app(audit_store=store)
    with TestClient(app) as c:
        yield c
    store.close()


# --------------------------------------------------------------------------- #
# Happy paths
# --------------------------------------------------------------------------- #
def test_index_has_the_form_and_the_flagship_button(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "<form" in r.text
    assert 'name="scenario_id"' in r.text
    assert "security-prompt-injection" in r.text
    assert "Prompt injection (flagship)" in r.text
    assert "no live LLM called" in r.text


def test_analyze_scenario_shows_both_panels_and_tags(client):
    r = client.post("/analyze", data={"scenario_id": "billing-double-charge"})
    assert r.status_code == 200
    assert "What the AI observed" in r.text
    assert "Deterministic decision" in r.text
    assert "chip--DETERMINISTIC" in r.text
    assert "chip--AI_OBSERVATION" in r.text
    assert "pill--BILLING" in r.text


def test_flagship_prompt_injection_shows_the_override(client):
    r = client.post("/analyze", data={"scenario_id": "security-prompt-injection"})
    assert r.status_code == 200
    # model said SUPPORT / LOW ...
    assert ">SUPPORT<" in r.text or "SUPPORT" in r.text
    # ... deterministic layer said SECURITY / CRITICAL
    assert "pill--SECURITY" in r.text
    assert "did not simply follow the model" in r.text
    assert "R02_SECURITY" in r.text


def test_analyze_free_text_renders(client):
    r = client.post("/analyze", data={"text": "the whole dashboard is down, nobody can log in"})
    assert r.status_code == 200
    assert "Deterministic decision" in r.text


def test_malformed_ai_output_is_human_review_not_a_guess(client):
    r = client.post("/analyze", data={"scenario_id": "malformed-ai-output"})
    assert r.status_code == 200
    assert "No usable interpretation" in r.text
    assert "pill--HUMAN_REVIEW" in r.text


# --------------------------------------------------------------------------- #
# Failure paths
# --------------------------------------------------------------------------- #
def test_empty_text_is_a_400_with_a_message(client):
    r = client.post("/analyze", data={"text": "   "})
    assert r.status_code == 400
    assert 'class="err"' in r.text


def test_oversize_text_is_a_400(client):
    r = client.post("/analyze", data={"text": "x" * 9000})
    assert r.status_code == 400


def test_unknown_scenario_id_is_a_400(client):
    r = client.post("/analyze", data={"scenario_id": "not-real"})
    assert r.status_code == 400
    assert "unknown scenario_id" in r.text


def test_get_only_pages_405_on_post(client):
    for path in ("/", "/audit", "/health"):
        assert client.request("POST", path).status_code == 405


def test_post_only_routes_405_on_get(client):
    assert client.get("/analyze").status_code == 405
    assert client.get("/api/analyze").status_code == 405


# --------------------------------------------------------------------------- #
# Audit visibility
# --------------------------------------------------------------------------- #
def test_audit_list_shows_processed_rows(client):
    client.post("/analyze", data={"scenario_id": "bug-with-repro"})
    client.post("/analyze", data={"scenario_id": "outage-cannot-login"})
    r = client.get("/audit")
    assert r.status_code == 200
    assert "<table" in r.text
    assert "pill--ENGINEERING" in r.text
    assert "pill--RELIABILITY" in r.text


def test_audit_detail_round_trips(client):
    rid = client.post("/api/analyze", json={"scenario_id": "data-loss-accidental-delete"}).json()[
        "request_id"
    ]
    r = client.get(f"/audit/{rid}")
    assert r.status_code == 200
    assert "AI interpretation" in r.text
    assert "keyword signals" in r.text.lower()
    assert "MENTIONS_DATA_LOSS" in r.text


def test_audit_detail_404_for_unknown_id(client):
    r = client.get("/audit/does-not-exist")
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Shareable demo links (read-only — no audit row written)
# --------------------------------------------------------------------------- #
def test_demo_link_renders_the_result(client):
    r = client.get("/demo/security-prompt-injection")
    assert r.status_code == 200
    assert "pill--SECURITY" in r.text
    assert "did not simply follow the model" in r.text


def test_demo_link_does_not_write_an_audit_row(client):
    before = len(client.get("/audit").text)
    client.get("/demo/billing-double-charge")
    r = client.get("/audit")
    # still the empty-state (no rows added by a demo view)
    assert "No requests processed yet" in r.text or len(r.text) == before


def test_demo_link_unknown_scenario_404(client):
    assert client.get("/demo/nope").status_code == 404


# --------------------------------------------------------------------------- #
# Health + API + security
# --------------------------------------------------------------------------- #
def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["provider"] == "mock"
    assert body["mode"] == "offline-mock"
    assert body["db_ok"] is True


def test_api_analyze_matches_the_pipeline(client):
    api = client.post("/api/analyze", json={"scenario_id": "conflicting-feature-vs-churn"}).json()
    pipe = process_request(
        TriageRequest(text="_", scenario_id="conflicting-feature-vs-churn"), MockProvider()
    )
    assert api["decision"]["route"] == pipe.decision.route.value
    assert api["decision"]["disposition"] == pipe.decision.disposition.value


def test_api_analyze_bad_input_is_400(client):
    r = client.post("/api/analyze", json={"text": ""})
    assert r.status_code == 400
    assert "error" in r.json()


def test_every_response_has_security_headers(client):
    r = client.get("/")
    assert r.headers["content-security-policy"].startswith("default-src 'self'")
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"


def test_pages_have_zero_external_resources(client):
    pages = [
        client.get("/").text,
        client.get("/audit").text,
        client.post("/analyze", data={"scenario_id": "support-howto"}).text,
        client.get("/demo/security-prompt-injection").text,
    ]
    for html in pages:
        assert "http://" not in html
        assert "https://" not in html
        assert "//cdn" not in html
        assert "<script" not in html  # no JavaScript anywhere
        # the only asset is the same-origin stylesheet
        assert html.count("<link") <= 1
        assert 'href="/static/triage.css"' in html


def test_static_css_is_served_same_origin(client):
    r = client.get("/static/triage.css")
    assert r.status_code == 200
    assert "text/css" in r.headers["content-type"]
    assert ".panel--decision" in r.text


def test_offline_label_is_on_every_page(client):
    assert "offline" in client.get("/").text.lower()
    assert "offline" in client.get("/audit").text.lower()
    assert "no live LLM" in client.post("/analyze", data={"scenario_id": "support-howto"}).text
