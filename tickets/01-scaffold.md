# 01 — Scaffold the package

Status: todo

Set up the repo so `just test` passes with the standard "plugin is installed" test.
Copy the house style from `~/work/simonw/datasette-alerts-ntfy` (smallest recent example).

## Files

- `pyproject.toml` — name `datasette-otel-otlp`, module `datasette_otel_otlp`,
  `version = "0.1.0a0"`, author Alex Garcia, `license = "Apache-2.0"`,
  classifier `Framework :: Datasette`, `requires-python = ">=3.10"`, setuptools backend.
  - `[project.entry-points.datasette] otel_otlp = "datasette_otel_otlp"`
  - dependencies: `datasette>=1a37` (bump to whichever alpha ships phase 1; until then
    dev runs editable — see PLAN.md gotcha), `opentelemetry-sdk>=1.37`,
    `opentelemetry-exporter-otlp-proto-http>=1.37`
  - `[dependency-groups] dev = ["pytest", "pytest-asyncio"]` plus the pytest asyncio
    ini options from datasette-alerts-ntfy's pyproject.
  - `[project.urls]` block pointing at github.com/datasette/datasette-otel-otlp.
- `datasette_otel_otlp/__init__.py` — empty shell with a `@hookimpl` no-op for now.
- `tests/test_datasette_otel_otlp.py` — the standard `test_plugin_is_installed` against
  `Datasette(memory=True)` hitting `/-/plugins.json`.
- `.gitignore` — copy datasette-alerts-ntfy's, trim the Vite/frontend section.
- `LICENSE` — Apache-2.0.
- Justfile stub: `test` recipe (`uv run --with-editable ~/projects/datasette pytest`);
  ticket 03 adds the demo recipes.

## Acceptance

- `just test` green.
- `uv run --with-editable . --with-editable ~/projects/datasette datasette --get /-/plugins.json`
  lists `datasette-otel-otlp`.
