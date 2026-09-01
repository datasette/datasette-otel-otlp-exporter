# 07 — Vendor presets: `grafana-cloud`

Status: done (scoped to the one preset with a real payoff; the rest stay a README table)

Sugar so config can read `preset: grafana-cloud` instead of a raw gateway URL plus a
hand-built basic-auth header. Motivated by the Fly.io research (2026-09-01): Grafana
Cloud's free tier (50 GB traces/mo, 14-day retention) is the only free managed trace
UI, and its endpoint/auth are exactly the kind of fiddly a preset should absorb.

## Shape

```yaml
plugins:
  datasette-otel-otlp:
    preset: grafana-cloud
    grafana_cloud:
      region: prod-us-east-0          # or endpoint: <full URL> to override
      instance_id: "123456"           # stack/instance id = basic-auth username
      api_token:
        $env: GRAFANA_CLOUD_TOKEN     # grafana.com token = basic-auth password
```

## Verified endpoint format (2026-09-01, grafana.com docs)

`https://otlp-gateway-<region>.grafana.net/otlp/v1/traces` — the **full per-signal
path**, because this plugin only appends `/v1/traces` to path-less endpoints. (The
docs' base `.../otlp` form relies on the SDK's env-var handling to append the signal
path.) Docs example region: `prod-us-east-0`; Grafana warns the host varies by when
the stack was created, hence the `endpoint:` override inside `grafana_cloud`.
Auth: HTTP basic, username = instance id, password = token →
`Authorization: Basic base64(instance_id:api_token)`.

## Decisions

- **Precedence**: OTEL_* env vars beat everything (unchanged); explicit
  `endpoint:`/`headers:` config beats the preset; explicit headers *merge over*
  preset headers key-by-key, so extras can be added and the auth header replaced.
- **Fail loudly**: unknown preset name or missing `region`/`instance_id`/`api_token`
  raises ValueError from the startup hook — `datasette serve` refuses to start
  rather than silently exporting nowhere. This fires even when env vars would
  override the preset (misconfiguration is misconfiguration).
- Preset options live under a key derived from the preset name
  (`grafana-cloud` → `grafana_cloud`); values are stringified (YAML/-s ints).
- Other vendors (honeycomb etc.) stay a README table — OTLP + one header is not
  worth code that can rot.

## Acceptance (met)

- Unit + integration tests in `tests/test_presets.py` assert the resolved
  endpoint/headers on the real OTLPSpanExporter via the lazy-exporter delegate — no
  network. Precedence (explicit config, env), merge semantics, and both failure
  modes covered. 24 passed, 1 skipped (cross-plugin, by design).
