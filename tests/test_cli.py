"""CLI — TEST-PLAN §2.4."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from ai_workflow_triage import __version__
from ai_workflow_triage.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_analyze_scenario_human_output_splits_ai_and_deterministic(capsys):
    code = main(["analyze", "--scenario", "billing-double-charge"])
    out = capsys.readouterr().out
    assert code == 0
    assert "WHAT THE AI OBSERVED" in out
    assert "DETERMINISTIC DECISION" in out
    assert "route             : BILLING" in out
    assert "[DETERMINISTIC]" in out
    assert "[AI OBSERVATION]" in out


def test_analyze_prompt_injection_shows_the_override(capsys):
    code = main(["analyze", "--scenario", "security-prompt-injection"])
    out = capsys.readouterr().out
    assert code == 0
    assert "urgency (model)   : LOW" in out
    assert "route             : SECURITY" in out


def test_analyze_text_runs_deterministic_keyword_path(capsys):
    # free text gets the mock's generic interpretation, but a deterministic
    # keyword (security) still routes it
    code = main(["analyze", "--text", "I think someone hacked our account"])
    out = capsys.readouterr().out
    assert code == 0
    assert "route             : SECURITY" in out


def test_analyze_free_text_without_keywords_goes_to_human_review(capsys):
    code = main(["analyze", "--text", "hey can you help me with the thing"])
    out = capsys.readouterr().out
    assert code == 0
    assert "route             : HUMAN_REVIEW" in out


def test_analyze_json_output(capsys):
    code = main(["analyze", "--scenario", "support-howto", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"]["route"] == "SUPPORT_TIER1"
    assert "audit_record" in payload
    assert "request_id" in payload


def test_analyze_writes_to_a_db(tmp_path, capsys):
    db = tmp_path / "audit.sqlite"
    code = main(["analyze", "--scenario", "bug-with-repro", "--db", str(db)])
    assert code == 0
    assert "audit persisted   : yes" in capsys.readouterr().out
    assert db.exists()

    from ai_workflow_triage.audit import SqliteAuditStore

    store = SqliteAuditStore(db)
    try:
        assert len(store.list_records()) == 1
    finally:
        store.close()


def test_analyze_oversize_text_errors():
    with pytest.raises(ValidationError):
        main(["analyze", "--text", "x" * 9000])


def test_validate_scenarios_ok(capsys):
    code = main(["validate-scenarios"])
    assert code == 0
    assert "15 synthetic scenarios" in capsys.readouterr().out


def test_unknown_scenario_id_is_a_clean_error(capsys):
    code = main(["analyze", "--scenario", "not-a-real-scenario"])
    assert code == 2
    assert "unknown scenario_id" in capsys.readouterr().err
