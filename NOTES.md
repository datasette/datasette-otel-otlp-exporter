# datasette-otel-otlp-exporter

[![PyPI](https://img.shields.io/pypi/v/datasette-otel-otlp-exporter.svg)](https://pypi.org/project/datasette-otel-otlp-exporter/)
[![Tests](https://github.com/datasette/datasette-otel-otlp-exporter/actions/workflows/test.yml/badge.svg)](https://github.com/datasette/datasette-otel-otlp-exporter/actions/workflows/test.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/datasette/datasette-otel-otlp-exporter/blob/main/LICENSE)

Datasette emits [OpenTelemetry](https://opentelemetry.io/) spans for every request and
SQL query — but core never configures an exporter, so by default those spans go
nowhere. This plugin is the missing exporter: install it, point it at any OTLP
backend with one line of config, and get traces. No `opentelemetry-instrument`
agent, no `OTEL_*` environment variable incantations.

```bash
datasette install datasette-otel-otlp-exporter
datasette mydb.db -s plugins.datasette-otel-otlp-exporter.endpoint http://localhost:4318
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
datasette mydb.db -s plugins.datasette-otel-otlp-exporter.endpoint http://localhost:4318
```

Load a page, wait a couple of seconds for the batch flush, and open the Jaeger
UI at **http://localhost:16686** — note the UI is on port 16686; port 4318 is
the OTLP ingest port and shows nothing in a browser. Pick the `datasette`
service and you get the full request waterfall, down to individual SQL
executions:

![A Jaeger trace of one Datasette table page: a GET request span with 53 child spans, mostly db.query spans a few hundred microseconds each](https://raw.githubusercontent.com/datasette/datasette-otel-otlp-exporter/main/.github/jaeger-trace.png)

## Configuration reference

All configuration lives under `plugins.datasette-otel-otlp-exporter` in
`datasette.yaml` (or via `-s` flags, as above):

```yaml
plugins:
  datasette-otel-otlp-exporter:
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

## Preset: Grafana Cloud

[Grafana Cloud's free tier](https://grafana.com/docs/grafana-cloud/send-data/otlp/)
(50 GB of traces/month, 14-day retention) is the cheapest way to get a real
trace UI without running anything yourself — handy for a machine on Fly.io,
where the platform offers no trace sink of its own. The `grafana-cloud`
preset builds the gateway endpoint and basic-auth header for you:

```yaml
plugins:
  datasette-otel-otlp-exporter:
    preset: grafana-cloud
    grafana_cloud:
      region: prod-us-east-0        # from your stack's OTLP config tile
      instance_id: "123456"         # ditto ("instance id" / stack id)
      api_token:
        $env: GRAFANA_CLOUD_TOKEN   # a grafana.com API token
```

On Fly: `fly secrets set GRAFANA_CLOUD_TOKEN=glc_...` and deploy. The values
come from your stack's **OpenTelemetry** configuration tile at grafana.com —
if your gateway host doesn't match the `otlp-gateway-<region>.grafana.net`
pattern (older stacks vary), set `endpoint:` inside `grafana_cloud` to the
full URL from the tile, ending in `/otlp/v1/traces`.

Explicit `endpoint`/`headers` config beats preset values (headers merge
per-key), and an unknown preset or missing field fails at startup rather
than exporting nowhere. The privacy warning above applies double here:
`preset:` is one config block that ships your users' SQL to a third party.

Other vendors don't need presets — they're just OTLP plus one header:

| Backend | `endpoint` | `headers` |
|---------|-----------|-----------|
| Honeycomb | `https://api.honeycomb.io` | `x-honeycomb-team: <key>` |
| Local Jaeger / collector | `http://localhost:4318` | — |

## Running alongside other OpenTelemetry setups

This plugin installs a `TracerProvider` only when nobody else has. If a real
provider already exists — `opentelemetry-instrument`, an embedding
application, or another exporter plugin such as
[datasette-otel-parquet](https://github.com/datasette/datasette-otel-parquet)
imported first — it attaches its span processor to that provider instead, so
OTLP export works the same whichever wiring got there first. In that attached
mode the provider owner's sampler and `service.name` apply, and the
`sample_ratio` / `service_name` config keys are ignored.

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
