"""`redact_secrets` — the safety net on error_detail / exception text."""

from __future__ import annotations

import pytest

from ai_workflow_triage.redaction import redact_secrets

# all fake; "EXAMPLE" marks each line so the repo secret-scanner ignores it
_SECRETS = [
    "sk-ant-EXAMPLEfake0123456789abcdef",
    "sk-EXAMPLEfake0123456789abcdef0123",
    "re_EXAMPLEfake0123456789abcdef",
    "AKIAEXAMPLE012345678",
    "api_key=EXAMPLEfake0123456789",
    "Authorization: Bearer EXAMPLEfake012345",
    "password = EXAMPLEpass2EXAMPLEpass2",
]


@pytest.mark.parametrize("payload", _SECRETS)
def test_secret_shapes_are_redacted(payload):
    cleaned = redact_secrets(f"upstream error, detail: {payload} — retry later")
    assert cleaned is not None
    assert "EXAMPLEfake" not in cleaned
    assert "EXAMPLEpass" not in cleaned
    assert "[REDACTED]" in cleaned
    assert "retry later" in cleaned  # non-secret tail is preserved


def test_ordinary_text_is_untouched():
    text = "the whole dashboard is down and none of us can log in"
    assert redact_secrets(text) == text


def test_none_and_empty_pass_through():
    assert redact_secrets(None) is None
    assert redact_secrets("") == ""
