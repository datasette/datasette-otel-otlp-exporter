# 04 — Tests: in-process OTLP receiver as a fixture

Status: todo

The datasette demo's `otlp_receiver.py` proved the wire format is decodable in ~30 lines
of handler; turn that idea into a pytest fixture and assert real exports end-to-end.

## Fixture

`otlp_server` — stdlib `HTTPServer` on an ephemeral port (port 0, read it back), thread
per test, collecting decoded spans into a list. Parse with
`opentelemetry-proto`'s `ExportTraceServiceRequest`, handle optional gzip. Borrow the
decode loop from `~/projects/datasette/demos/otel/otlp_receiver.py` (attribute_value +
triple loop), not the signal/summary machinery.

## Tests

- **Export happens**: `Datasette(memory=True, plugin_config={endpoint: fixture url})`,
  make one request via `datasette.client`, force-flush the provider, assert the fixture
  saw a `GET ...` span and ≥1 `db.query` span with `db.system == "sqlite"`.
- **Headers**: configure `headers: {x-test: "1"}`, assert the fixture's handler saw it.
- **service_name**: assert resource attribute `service.name` on the wire.
- **Dormant without endpoint**: no config → no listener traffic, no crash.
- **Respects existing provider**: install an SDK provider first, then init the plugin →
  plugin leaves it alone (assert by identity).
- **sample_ratio 0.0** → fixture receives nothing after a request + flush.

## Gotchas

- `set_tracer_provider()` is once-per-process in otel — tests that need different
  provider states must either monkeypatch `trace._TRACER_PROVIDER` (reset fixture) or
  run in subprocesses. Decide once, write a `reset_otel` autouse fixture, keep it in one
  place.
- Needs editable datasette from the phase-1 branch (PLAN.md gotcha) — CI (ticket 06)
  must install datasette from the git branch until an alpha ships.

## Acceptance

- `just test` green, including at least the export/headers/dormant/sample tests.
