"""Regex secret scanner — a fast pre-commit / CI gate.

What it catches: obvious key shapes (Anthropic / OpenAI / Resend / AWS), private
key headers, and `NAME=secret` / `NAME: secret` assignments for a small set of
sensitive names. What it does NOT catch: high-entropy strings with no tell,
secrets split across lines, or anything base64-wrapped. `SECURITY.md` says to run
a full-history tool (detect-secrets / gitleaks) before publication — this script
is the quick everyday check, not that gate.

    python scripts/scan_secrets.py           # scan the repo, exit 1 on a hit
    python scripts/scan_secrets.py PATH ...   # scan specific paths
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    ".mypy_cache",
    "htmlcov",
}
_SKIP_SUFFIXES = {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".woff", ".woff2"}

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai-style key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("resend key", re.compile(r"\bre_[A-Za-z0-9_\-]{20,}")),
    ("aws access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    (
        "private key header",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    ),
    (
        "secret assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd|client[_-]?secret)\b\s*[:=]\s*"
            # a quoted 12+ char literal, or a bare 16+ char token that mixes
            # letters and digits (i.e. not a plain identifier like self._api_key)
            r"(?:'[^']{12,}'|\"[^\"]{12,}\"|"
            r"(?=[A-Za-z0-9/_+\-.]*[0-9])(?=[A-Za-z0-9/_+\-.]*[A-Za-z])[A-Za-z0-9/_+\-.]{16,})"
        ),
    ),
]

# Lines that are obviously safe examples / placeholders.
_ALLOW = re.compile(
    r"(?i)(REDACTED|placeholder|example|dummy|fake|your[_-]?key|xxxx|<[a-z_]+>|=\s*$|=\s*['\"]?\s*['\"]?$)"
)


def _iter_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for base in paths:
        if base.is_file():
            files.append(base)
            continue
        for path in base.rglob("*"):
            if path.is_dir():
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if path.suffix.lower() in _SKIP_SUFFIXES:
                continue
            files.append(path)
    return files


def scan(paths: list[Path]) -> list[str]:
    hits: list[str] = []
    self_path = Path(__file__).resolve()
    for path in _iter_files(paths):
        if path.resolve() == self_path:
            continue  # this file contains the patterns themselves
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _ALLOW.search(line):
                continue
            for label, pattern in _PATTERNS:
                if pattern.search(line):
                    rel = path.relative_to(_ROOT) if _ROOT in path.resolve().parents else path
                    hits.append(f"{rel}:{lineno}: possible {label}")
    return hits


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    paths = [Path(a) for a in args] or [_ROOT]
    hits = scan(paths)
    if hits:
        print("POTENTIAL SECRETS FOUND:")
        for hit in hits:
            print(f"  {hit}")
        return 1
    print("scan_secrets: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
