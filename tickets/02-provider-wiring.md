# 02 — Provider + OTLP exporter wiring from plugin config

Status: done

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

### Measured answer (2026-08-31, opentelemetry-sdk 1.44)

Option 2 (two-phase), and option 1 would have lost more than one span:

- A span started through the ProxyTracer before `set_tracer_provider()` is a
  NonRecordingSpan forever — it is NOT retroactively recorded when the provider
  arrives. `datasette.startup` *starts* before any hook runs and *ends* after the
  `startup()` hooks, so option 1 loses the startup span **and** its ~20 children
  (catalog refresh db.query spans etc.). Not "just one span".
- `BatchSpanProcessor` only sees spans that **end** after it is attached →
  attaching it at import (wrapped around a lazy exporter, `_LazySpanExporter`)
  captures the whole startup trace; the lazy exporter buffers any export that
  fires before config is readable (bounded at 4096 spans).
- Spans hold the provider's `Resource` **by reference**, so `service_name` config
  is retrofitted onto the queued startup trace by swapping
  `resource._attributes` (BoundedAttributes is immutable; replace, don't mutate).
- Sampling decisions are made at span start and cannot be revoked: `sample_ratio`
  therefore applies from the startup hook onward. The startup trace itself is
  always sampled (default ParentBased(ALWAYS_ON), swapped via a delegating
  `_DeferredSampler`). Documented trade-off.
- Env precedence is implemented by *omission*: `OTLPSpanExporter(endpoint=...)`
  beats env vars in the SDK, so when `OTEL_EXPORTER_OTLP{_TRACES,}_ENDPOINT` /
  `_HEADERS` / `OTEL_SERVICE_NAME` / `OTEL_TRACES_SAMPLER` are set the plugin
  simply doesn't pass the corresponding argument and the SDK's env handling wins.
- Dormant mode (no endpoint anywhere): deferred sampler → `ALWAYS_OFF`, lazy
  exporter → discard, one stderr line, `mode = "dormant"`.

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
