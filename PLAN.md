# datasette-otel-otlp

A Datasette plugin that turns the OpenTelemetry spans Datasette core emits into a real
OTLP export, with one `datasette install` and at most a few lines of config — no
`opentelemetry-instrument` agent, no environment-variable incantations.

## Context

Datasette's phase-1 OpenTelemetry stack (simonw/datasette PRs #2862–#2864, demo in #2894)
ships `opentelemetry-api` only: core emits spans but never installs a `TracerProvider`,
exporter, or sampler. Turning tracing on today means running under the
`opentelemetry-instrument` agent with ~7 env vars. Most users won't.

This plugin is **Option A** from the brainstorm in the datasette repo's
`plans/otel-0831/README.md` (git-excluded, local): the "point it somewhere" OTLP exporter.
The zero-config `/-/traces` viewer tier (Options B/C there) is deliberately **not** this
repo — if built, it becomes a separate plugin that can depend on this one.

**Why this works without the agent:** `datasette/telemetry.py` resolves its tracer at
module import, but the OTel API returns a ProxyTracer that forwards once anyone calls
`trace.set_tracer_provider()`. Plugins load before `invoke_startup()`, so wiring the
provider at plugin import time catches everything, including the `datasette.startup`
span. (Verified 2026-08-31 against the phase-1 branch.)

## What it looks like to a user

```bash
datasette install datasette-otel-otlp
datasette mydb.db -s plugins.datasette-otel-otlp.endpoint http://localhost:4318
```

or in `datasette.yaml`:

```yaml
plugins:
  datasette-otel-otlp:
    endpoint: http://localhost:4318   # any OTLP/HTTP backend: Jaeger, Tempo, vendors
    # everything below optional
    headers:
      x-honeycomb-team: $HONEYCOMB_KEY
    service_name: my-datasette        # default: "datasette"
    sample_ratio: 1.0                 # default: 1.0
```

Jaeger v1.35+/v2, Tempo, Honeycomb, Datadog agent, etc. all ingest OTLP natively — one
exporter covers effectively every backend; per-vendor exporter packages are deprecated.

## Design decisions (made up front)

- **OTLP/HTTP protobuf only** (`opentelemetry-exporter-otlp-proto-http`). No gRPC in v1:
  the gRPC exporter drags in `grpcio`, and every OTLP backend speaks HTTP.
- **Provider wiring happens at module import time**, not in the `startup()` hook, so the
  `datasette.startup` span is captured. Config isn't available at import time, so import
  installs the provider with a deferred/env-configured exporter and the plugin finalizes
  exporter settings from plugin config as early as a hook allows — exact split is ticket 02's
  main design question.
- **Respect standard `OTEL_*` env vars** where set; plugin config wins over defaults but
  explicit env vars win over plugin config (someone running under a real agent should not
  fight the plugin). If a provider is already installed (agent user), do nothing loudly
  (one stderr line), not nothing silently.
- **Traces only** in v1. No metrics, no logs, no multi-exporter fan-out, no sampling knobs
  beyond one ratio.
- **Privacy is a README headline, not a footnote**: `db.query.text` is recorded on spans
  and is user-supplied SQL on public instances; this plugin makes shipping it off-box a
  one-liner. Parameter values are never recorded (only counts) — say both things loudly.

## Dev environment gotcha

The spans this plugin exports exist only on the datasette PR branches, not on any
released datasette. All dev/demo commands must run against an editable install of
`~/projects/datasette` **checked out on `asg017/otel-phase1-4-otlp-demo`** (or later in
that stack). The Justfile hardcodes `--with-editable ~/projects/datasette`; revisit when
phase 1 ships in an alpha.

## Tickets

Work them in order; each is self-contained with acceptance criteria.

| # | Ticket | Status |
|---|--------|--------|
| 01 | [Scaffold the package](tickets/01-scaffold.md) | done |
| 02 | [Provider + OTLP exporter wiring from plugin config](tickets/02-provider-wiring.md) | done |
| 03 | [Justfile: demo commands against Jaeger and the receiver](tickets/03-justfile-demos.md) | done |
| 04 | [Tests: in-process OTLP receiver as a fixture](tickets/04-tests.md) | done |
| 05 | [README: quickstart, config reference, privacy](tickets/05-readme.md) | done |
| 06 | [CI: test + publish workflows](tickets/06-ci.md) | done (pending first push) |
| 07 | [Stretch: vendor presets](tickets/07-presets.md) | todo (stretch — skip for 0.1) |
| 08 | [Attach-don't-abdicate: coexistence with other exporter plugins](tickets/08-coexistence.md) | done |

## Reference material

- `~/projects/datasette/demos/otel/` — the Justfile env vars, the 150-line
  `otlp_receiver.py` (reuse as a test fixture in ticket 04), and README language to steal.
- `~/work/simonw/datasette-alerts-ntfy` — smallest recent example of the house plugin
  style: pyproject shape, entry point, Justfile `uv run --with-editable .` pattern, test
  bootstrap, `.github/workflows/{test,publish}.yml`.
- `~/projects/datasette/plans/otel-0831/README.md` — the full brainstorm this scopes from.
