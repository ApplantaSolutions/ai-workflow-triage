"""Deterministic signal extraction from the raw request text.

This is the independent check on the model. The rule engine consumes both the
validated ``TriageInterpretation`` *and* these signals; when they disagree,
that disagreement is itself a routing signal (R11).

The families are deliberately biased toward **over-triage**: a false "this looks
like security" routes a benign message to a queue where a human corrects it
(a nuisance); a false negative would be worse. The regexes are simple and, in
principle, gameable — they are documented as heuristics, not a security control,
and are paired with the model's independent read and the conflict gate.
"""

from __future__ import annotations

import re

from ..models import KeywordSignals

# family name -> list of case-insensitive patterns
_FAMILIES: dict[str, list[str]] = {
    "security_language": [
        r"hacked?",
        r"\bhack\b",
        r"data breach",
        r"\bbreach(ed|es)?\b",
        r"unauthori[sz]ed access",
        r"phishing",
        r"compromis(e|ed|ing)",
        r"suspicious login",
        r"someone (signed|logged) in",
        r"signed into my .{0,40} account from",
        r"security (issue|hole|flaw|vulnerabilit\w*|concern|problem)",
        r"view (other|another|others'?) (customers?|users?|people'?s?)?'?s? ?(invoices?|data|accounts?|records?)",
        r"see (other|others'?|everyone'?s) (invoices?|data|accounts?|records?)",
        r"access (other|another) (customers?|users?|accounts?|people)",
        r"changing the url",
        r"\bidor\b",
        r"leak(ed|ing)? (customer|user|account|our) (data|info|information)",
        r"is our (customer )?data safe",
        r"did we get hacked",
    ],
    "injection_language": [
        r"ignore (all |the )?previous instructions",
        r"disregard (the |all )?(above|previous)",
        r"system instruction",
        r"important system instruction",
        r"\bsystem prompt\b",
        r"you are now",
        r"as an ai( language)?( model)?",
        r"classify this (message )?as",
        r"do not escalate",
        r"mark this (as |message )?low",
        r"SYSTEM:\s",
        r"set (the )?(urgency|category|priority) to",
    ],
    "billing_language": [
        r"charged? (me )?twice",
        r"charged twice",
        r"double[- ]?charg(e|ed)",
        r"duplicate charge",
        r"\brefund\b",
        r"\binvoice\b",
        r"overcharg(e|ed)",
        r"billed twice",
        r"two identical charges",
        r"charged .{0,15}(again|a second time)",
        r"wrong amount",
        r"billing (issue|problem|error)",
        r"double billed",
    ],
    "outage_language": [
        r"\bis down\b",
        r"\bare down\b",
        r"\bwas down\b",
        r"everything is (broken|down)",
        r"can'?t log ?in",
        r"cannot log ?in",
        r"couldn'?t log ?in",
        r"unable to log ?in",
        r"won'?t let (me|us) log ?in",
        r"(none of us|no one|nobody|not one of us)\b.{0,20}log ?in",
        r"can'?t (get in|access (the|our) (account|dashboard|system))",
        r"\b50[0-9]\b",
        r"\b50[0-9]s\b",
        r"service unavailable",
        r"\boutage\b",
        r"whole (dashboard|site|system|app|platform) is down",
        r"spinning wheel",
        r"returning (500|502|503|errors|5xx)",
        r"down for (everyone|all|the whole team)",
        r"integration is down",
    ],
    "data_loss_language": [
        r"deleted (everything|all|our|two years|years of|my projects)",
        r"lost (all|our|everything|two years|years of|the)( data| work| projects)?",
        r"there'?s no undo",
        r"\bno undo\b",
        r"no way to (get it back|recover|undo)",
        r"accidental(ly)? delet\w*",
        r"wiped (all|our|everything)",
        r"gone forever",
        r"tell me there'?s a backup",
        r"is there (any |a )?backup",
        r"hit delete",
    ],
    "legal_language": [
        r"our lawyer",
        r"\battorney\b",
        r"\bgdpr\b",
        r"\bccpa\b",
        r"legal action",
        r"\bsue\b",
        r"\blawsuit\b",
        r"data protection (officer|request)",
        r"right to be forgotten",
        r"subject access request",
        r"breach of contract",
    ],
    "churn_language": [
        r"cancel(l?ing|l?ed|l)? (our|the|my) (contract|subscription|account|plan)",
        r"going to cancel",
        r"will cancel",
        r"we are cancel\w*",
        r"we'?re cancel\w*",
        r"switching to (a )?competitor",
        r"moving to (a )?competitor",
        r"take our business elsewhere",
        r"won'?t renew",
        r"not renew(ing)?",
        r"cancel(ling)? and mov(e|ing)",
    ],
    "deadline_language": [
        r"by (end of day|eod|cob|close of business)",
        r"by (this |next )?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)",
        r"by (tomorrow|tonight|noon|midnight|\d{1,2}\s?(am|pm))",
        r"in \d+ ?(min|mins|minute|minutes|hour|hours|hr|hrs|day|days)",
        r"within (the )?(hour|day|next \d+)",
        r"\bdeadline\b",
        r"demo in \d+",
        r"(call|meeting|demo|launch|presentation) in \d+",
        r"before (the |our )?(call|meeting|demo|launch|presentation)",
        r"due (by|on|tomorrow|today|friday|monday)",
    ],
}

# "not / isn't ... <family match>" — kept anyway (over-triage bias) but noted.
_NEGATION_BEFORE = re.compile(
    r"\b(not|isn'?t|no longer|hardly|wouldn'?t call it)\b[\w\s'\-]{0,15}$", re.IGNORECASE
)

_COMPILED: dict[str, list[re.Pattern[str]]] = {
    family: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
    for family, patterns in _FAMILIES.items()
}

# The eight boolean family names, in declaration order.
FAMILY_NAMES: tuple[str, ...] = tuple(_FAMILIES)


def extract_signals(text: str) -> KeywordSignals:
    """Re-derive the deterministic signal set from ``text``."""

    matched: dict[str, list[str]] = {}
    notes: list[str] = []
    flags: dict[str, bool] = {}

    for family, regexes in _COMPILED.items():
        hits: list[str] = []
        lowered_hits: set[str] = set()
        for regex in regexes:
            for match in regex.finditer(text):
                fragment = match.group(0).strip()
                if not fragment or fragment.lower() in lowered_hits:
                    continue
                hits.append(fragment)
                lowered_hits.add(fragment.lower())
                if family == "security_language" and _NEGATION_BEFORE.search(text[: match.start()]):
                    notes.append(
                        f"security language {fragment!r} matched inside an apparent negation; "
                        "kept (deliberate over-triage bias)"
                    )
        flags[family] = bool(hits)
        if hits:
            matched[family] = hits

    return KeywordSignals(matched=matched, notes=notes, **flags)
