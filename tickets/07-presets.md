# 07 — Stretch: vendor presets

Status: todo (stretch — skip for 0.1)

Sugar so config can read `preset: honeycomb` instead of a raw endpoint + auth header
name. A preset is only `{endpoint, header_name_for_auth, docs_url}`.

## Candidate presets

- `jaeger` → `http://localhost:4318`, no auth (mostly a docs affordance)
- `honeycomb` → `https://api.honeycomb.io`, `x-honeycomb-team`
- `grafana-cloud` → per-stack endpoint (needs a `stack` param — maybe not worth it)
- `otel-collector` → `http://localhost:4318` (alias of jaeger, different docs)

## Open questions

- Is this worth it at all, versus a README table of "endpoint + header per vendor"?
  Decide after 0.1 feedback; a README table costs nothing and can't rot into wrong code.

## Acceptance

- `preset: honeycomb` + `headers: {x-honeycomb-team: $KEY}` exports successfully, or the
  ticket is closed as "README table instead".
