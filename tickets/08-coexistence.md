# 08 — Attach-don't-abdicate: coexistence with other exporter plugins

Status: done

## Why

The original `_install()` treated any pre-existing real SDK `TracerProvider` as
"running under the agent" and went `mode="foreign"` — do nothing, loudly. That
heuristic broke the moment a second exporter plugin existed:
datasette-otel-parquet's ticket 02 measured (2026-09-01, its
`test_coexistence.py`) that with both plugins installed, behavior depended on
entry-point import order, which is not guaranteed:

- otlp first → parquet attaches to otlp's provider, both export. Fine.
- parquet first → otlp saw a real provider, went foreign, and **exported
  nothing even when configured**. Silent, order-dependent breakage.

A second measured hazard: otlp installed with no endpoint swapped its sampler
to `ALWAYS_OFF` at startup — which starved every *other* processor attached to
its provider too (parquet attached to a dormant-otlp-owned provider recorded
nothing).

## The change

Mirrors the parquet plugin's `_install()` shape (`owns_provider` in `_state`;
modes `pending`/`active`/`dormant`/`inert`, `foreign` retired):

1. **Real SDK provider already installed** (agent, or another exporter plugin
   imported first): create the `_LazySpanExporter` + `BatchSpanProcessor` and
   `add_span_processor()` onto it instead of stepping aside. In attached mode
   there is no sampler or Resource ownership — the owner's sampler and
   `service.name` apply, and the `sample_ratio`/`service_name` config keys are
   ignored (silently, same as parquet; documented in the README).
2. **Dormant while attached**: `exporter.configure(None)` + one stderr line;
   the sampler is never touched — it isn't ours.
3. **Dormant as owner**: the `ALWAYS_OFF` swap (a pure optimization for the
   unconfigured-install case) now happens only when our processor is the
   **sole** processor on the provider. Detection reads
   `provider._active_span_processor._span_processors == (processor,)` —
   private, version-dependent API (verified on opentelemetry-sdk 1.44),
   guarded by try/except. When the answer is unknowable, sampling stays on:
   correctness beats the optimization, and the only cost is that an
   unconfigured install records spans nobody exports.
4. **Non-SDK provider** (can't `add_span_processor`): `mode="inert"`, one
   stderr line, do nothing — the only remnant of the old foreign behavior.

## Tests

`tests/test_coexistence.py`: attached-mode export alongside the owner's own
pipeline; dormant-attached never touches the sampler; dormant-owner with a
co-attached processor keeps sampling on; dormant-sole-owner still goes
`ALWAYS_OFF`; and the parquet-first order end-to-end (skips unless
datasette-otel-parquet is importable — `just test-both` wires the sibling
checkout in). The parquet repo's `test_coexistence.py` was updated in the same
change to pin the fixed behavior from its side.

## Follow-up that would delete all of this

The real fix is core owning install-or-attach once: a lazily-importing
`datasette.tracing.add_span_processor()` in datasette itself. Being explored
separately; if it lands, both plugins' provider machinery collapses into one
call.
