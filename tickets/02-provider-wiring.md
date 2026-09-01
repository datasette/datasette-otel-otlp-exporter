# 02 — Provider + OTLP exporter wiring from plugin config

Status: todo

The heart of the plugin: install a `TracerProvider` with a `BatchSpanProcessor` +
`OTLPSpanExporter` (http/protobuf), configured from plugin config, early enough to catch
`datasette.startup`.

## Design question to settle first (spike, ~30 min)

Config lives on the `Datasette` instance, which doesn't exist at module import time, but
the provider ideally exists before the first span. Options, in preference order:

1. **Wire everything in the earliest hook that sees config** (probably `startup()` or
   wherever `plugin_config()` is first readable). Measure what's actually lost: if the
   ProxyTracer means only spans *started* before `set_tracer_provider()` are dropped, and
   the first span is `datasette.startup` itself, losing just that one span may be an
   acceptable v1 trade — document it and move on.
2. **Two-phase**: at import, `set_tracer_provider()` with a provider whose exporter is a
   small lazy wrapper; fill in endpoint/headers once config is readable, buffering
   nothing before that (BatchSpanProcessor already queues).
3. Env-var-only at import (no plugin config) — rejected, that's just the agent again.

Record the measured answer in this ticket when done.

## Config schema

`plugin_config("datasette-otel-otlp")`:

- `endpoint` (str, required for export; without it the plugin logs one line and stays
  dormant): base OTLP/HTTP endpoint, e.g. `http://localhost:4318`. Append `/v1/traces`
  ourselves if the path is absent.
- `headers` (dict, optional): forwarded verbatim to the exporter (vendor auth). Support
  `$ENV_VAR` values via Datasette's existing config env substitution — verify that works
  for nested dicts.
- `service_name` (str, default `"datasette"`): sets `service.name` resource attribute.
- `sample_ratio` (float, default 1.0): `TraceIdRatioBased` sampler via `ParentBased`.

## Precedence rules

- Explicit `OTEL_*` env vars beat plugin config (someone running the real agent must not
  fight the plugin).
- If `trace.get_tracer_provider()` is already a real SDK provider (agent user), print one
  stderr line and do nothing else.

## Acceptance

- With `endpoint` configured and Jaeger running, one request lands as a `GET <route>`
  trace in Jaeger with `service.name` from config. (Manual, via ticket 03's Justfile.)
- Without config: zero behavior change, zero noise beyond at most one log line.
- Headers reach the wire (assert via ticket 04's test receiver).
- `sample_ratio: 0.0` exports nothing; `1.0` exports everything.
