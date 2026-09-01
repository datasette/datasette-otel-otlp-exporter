# 03 — Justfile: demo commands against Jaeger and the receiver

Status: todo

Mirror `~/projects/datasette/demos/otel/Justfile`, but with the plugin doing the wiring —
the point of the demo is that the seven `OTEL_*` env vars from that Justfile are **gone**.

## Recipes

- `default` — `@just --list --unsorted`.
- `test` — from ticket 01.
- `demo-db` (internal) — generate `demo.db` (200-row table via sqlite3 recursive CTE,
  same SQL as the datasette demo) if missing. `demo.db` is gitignored.
- `dev` — the datasette-alerts-ntfy `dev` pattern:
  `uv run --with-editable . --with-editable ~/projects/datasette --isolated --refresh
  datasette demo.db -s plugins.datasette-otel-otlp.endpoint http://localhost:4318 -p 8001`.
  Accept `*options` passthrough.
- `jaeger` — run the `jaeger` binary (guard with a friendly "not found" message + download
  URL); UI note: http://localhost:16686, OTLP ingest on 4318 has no UI.
- `receiver` — run datasette's demo receiver for UI-less verification:
  `uv run --with opentelemetry-proto python ~/projects/datasette/demos/otel/otlp_receiver.py`
  (or vendor a copy if the path coupling annoys — decide here).
- `request` — `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8001/demo/plants`.

## Acceptance

- `just jaeger` + `just dev` + `just request` → trace visible in Jaeger UI, service
  `datasette`, no `OTEL_*` env vars anywhere in the Justfile.
- `just receiver` + `just dev` + `just request` → receiver summary shows the request
  span + db.query spans on Ctrl-C.
