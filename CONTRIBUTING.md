# Contributing

This is a personal portfolio project. It is not looking for feature contributions,
but issues and small fixes are welcome.

## Development

```bash
python -m venv .venv && . .venv/Scripts/activate   # or source .venv/bin/activate
pip install -e ".[dev]"
```

## Before opening a PR

Everything CI runs, in order:

```bash
ruff check .
ruff format --check .
pytest
python scripts/build_examples.py --check     # committed audit examples are byte-stable
python scripts/scan_secrets.py
```

Or `make` targets: `make lint format-check test examples-check secret-scan`.

## Ground rules

- **The deterministic engine (`rules/`, `decision.py`, `models.py`) is the
  authority.** Don't move decision logic into the pipeline or the web layer.
- **A model failure must never produce a confident route.** Every unusable-AI
  path lands on `HUMAN_REVIEW`; tests enforce this.
- **All test / example / scenario data is synthetic.** No real names, emails,
  companies, or data from anywhere else.
- **No secrets** in code, tests, fixtures, or commit messages. `.env` is
  git-ignored; `.env.example` is names-only.
- Line endings are LF (`.gitattributes`); every generated file is written with
  `newline="\n"`.
- New rules get a fires / doesn't-fire test in isolation **and** a precedence
  test if they interact with the ordering.
