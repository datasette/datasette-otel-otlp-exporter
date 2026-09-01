# datasette-otel-otlp

[![PyPI](https://img.shields.io/pypi/v/datasette-otel-otlp.svg)](https://pypi.org/project/datasette-otel-otlp/)
[![Tests](https://github.com/datasette/datasette-otel-otlp/actions/workflows/test.yml/badge.svg)](https://github.com/datasette/datasette-otel-otlp/actions/workflows/test.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/datasette/datasette-otel-otlp/blob/main/LICENSE)

Datasette emits [OpenTelemetry](https://opentelemetry.io/) spans for every request and
SQL query — but core never configures an exporter, so by default those spans go
nowhere. This plugin is the missing exporter: install it, point it at any OTLP
backend with one line of config, and get traces. No `opentelemetry-instrument`
agent, no `OTEL_*` environment variable incantations.

```bash
datasette install datasette-otel-otlp
datasette mydb.db -s plugins.datasette-otel-otlp.endpoint http://localhost:4318
```

Jaeger, Grafana Tempo, Honeycomb, the OpenTelemetry Collector, Datadog's agent
and effectively every other tracing backend ingest OTLP/HTTP natively, so this
one exporter covers all of them.

## ⚠️ Privacy: this ships SQL text off-box

Datasette records the SQL text of every query as the `db.query.text` span
attribute — and on a public Datasette instance that includes **arbitrary
user-supplied SQL** typed into `?sql=` or the query editor. Configuring this
plugin sends all of it to whatever endpoint you name, so treat your tracing
backend as containing whatever your users type.

Parameter *values* are never recorded — only parameter counts — so data in your
tables does not leak into spans through query parameters. But the SQL text
itself, and your table/column names inside it, do go over the wire.

## Quickstart with Jaeger

[Jaeger](https://www.jaegertracing.io/) ships as a single binary — no Docker
needed. [Download it](https://www.jaegertracing.io/download/), run `jaeger`,
then start Datasette with the plugin configured:

```bash
jaeger &
datasette mydb.db -s plugins.datasette-otel-otlp.endpoint http://localhost:4318
```

Load a page, wait a couple of seconds for the batch flush, and open the Jaeger
UI at **http://localhost:16686** — note the UI is on port 16686; port 4318 is
the OTLP ingest port and shows nothing in a browser. Pick the `datasette`
service and you get the full request waterfall, down to individual SQL
executions:

![A Jaeger trace of one Datasette table page: a GET request span with 53 child spans, mostly db.query spans a few hundred microseconds each](https://raw.githubusercontent.com/datasette/datasette-otel-otlp/main/.github/jaeger-trace.png)

## Configuration reference

All configuration lives under `plugins.datasette-otel-otlp` in
`datasette.yaml` (or via `-s` flags, as above):

```yaml
plugins:
  datasette-otel-otlp:
    endpoint: http://localhost:4318   # required for export; the only mandatory key
    headers:                          # optional - vendor auth etc.
      x-honeycomb-team:
        $env: HONEYCOMB_KEY
    service_name: my-datasette        # optional, default "datasette"
    sample_ratio: 0.25                # optional, default 1.0
```

| Key | Default | Meaning |
|-----|---------|---------|
| `endpoint` | *(none — plugin stays dormant)* | Base OTLP/HTTP URL, e.g. `http://localhost:4318` or `https://api.honeycomb.io`. `/v1/traces` is appended automatically when the URL has no path. |
| `headers` | `{}` | HTTP headers sent with every export, verbatim — this is where vendor API keys go. Use Datasette's `{"$env": "VAR_NAME"}` substitution to keep secrets out of config files (it works on nested values, as above). |
| `service_name` | `datasette` | The `service.name` resource attribute — how the instance is labeled in your tracing UI. |
| `sample_ratio` | `1.0` | Head sampling: the fraction of traces kept, via a parent-based `TraceIdRatioBased` sampler. `0.0` exports nothing. One caveat: the once-per-process startup trace begins before plugin config is readable and is always sampled. |

Without an `endpoint` the plugin logs one line at startup and does nothing —
installing it does not change behavior until you configure it.

## Running under the real agent

If you already run Datasette under `opentelemetry-instrument`, or embed it in
an application that installs its own `TracerProvider`, you don't need this
plugin — and it knows: on finding an existing provider it prints one line to
stderr and steps aside entirely.

Standard `OTEL_*` environment variables also take precedence over plugin
config on a per-setting basis: `OTEL_EXPORTER_OTLP_ENDPOINT` /
`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS` /
`OTEL_EXPORTER_OTLP_TRACES_HEADERS`, `OTEL_SERVICE_NAME` and
`OTEL_TRACES_SAMPLER` each override the corresponding config key when set, so
an operator's environment always beats a config file.

## What spans you get

Whatever Datasette emits — this plugin adds no spans of its own, it only
exports. One span per HTTP request named `GET <route>`, one per SQL query with
`db.query.text` / `db.namespace` / timing attributes, write-queue spans, and a
`datasette.startup` trace covering plugin hooks and catalog initialization
(captured because this plugin wires the provider at import time, before
startup runs). The full span and attribute table lives in
[Datasette's telemetry documentation](https://docs.datasette.io/en/latest/internals.html#internals-telemetry).

Traces only in this release: no metrics, no logs.

## Development

Tests and demos run against an editable install of Datasette from the
phase-1 OpenTelemetry branch — see the `Justfile` (`just test`, and
`just jaeger` / `just receiver` + `just dev` + `just request` for the demo
flow described above).
