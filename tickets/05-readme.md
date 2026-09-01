# 05 — README: quickstart, config reference, privacy

Status: todo

Steal tone and structure from `~/projects/datasette/demos/otel/README.md` (already
battle-tested language) and the house plugin README shape.

## Sections

- One-paragraph pitch: Datasette emits OpenTelemetry spans but configures nothing; this
  plugin is the missing exporter. Install + one config line → traces in any OTLP backend.
- **Quickstart with Jaeger** (the cool-UI path): jaeger binary (no Docker), the one-line
  config, screenshot of the request waterfall. Note explicitly: the UI is on :16686 —
  :4318 is the ingest port and shows nothing.
- **Configuration reference**: endpoint / headers / service_name / sample_ratio table,
  incl. `$ENV_VAR` substitution for secrets in headers.
- **Running under the real agent**: if you already use `opentelemetry-instrument`, you
  don't need this plugin; the plugin detects an existing provider and steps aside.
- **Privacy** (headline, not footnote): `db.query.text` is recorded and is user-supplied
  SQL on public instances — exporting sends it off-box. Parameter values are never
  recorded, only counts.
- **What spans you get**: link to datasette's telemetry docs section rather than
  duplicating the span table.

## Acceptance

- README renders clean on GitHub; quickstart verified by actually following it top to
  bottom in a fresh terminal.
