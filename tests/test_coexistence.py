"""
Ticket 08: attach-don't-abdicate. When a real SDK TracerProvider already
exists (the agent, or another exporter plugin imported first), this plugin
attaches its processor instead of stepping aside; when dormant, it only
switches sampling off if it owns the provider AND nothing else is attached.

These tests re-run _install() after unwinding OpenTelemetry's set-once
global, simulating the possible wiring orders. Spans are then emitted
through ``trace.get_tracer(...)`` against the fresh global provider rather
than through Datasette requests: datasette's modules bind their module-level
tracer to whichever provider was live at this process's first span (the
conftest snapshot provider), so datasette-emitted spans cannot reach a
provider installed mid-run. That is a test-process artifact only - what
these tests verify is the wiring topology; end-to-end span flow is
test_export.py's job.
"""

import pytest
from conftest import reset_tracer_state
from datasette.app import Datasette
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF

import datasette_otel_otlp_exporter


async def run_startup(endpoint=None):
    "Let the startup() hook resolve config, the real code path."
    config = None
    if endpoint is not None:
        config = {"plugins": {"datasette-otel-otlp-exporter": {"endpoint": endpoint}}}
    datasette = Datasette([], memory=True, config=config)
    await datasette.invoke_startup()
    return datasette


def emit_span(name):
    "A span through the CURRENT global provider (not datasette's bound tracer)."
    with trace.get_tracer("coexistence-test").start_as_current_span(name):
        pass


def agent_style_provider():
    "A provider some other machinery installed, with its own pipeline."
    collected = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(collected))
    trace.set_tracer_provider(provider)
    return provider, collected


@pytest.mark.asyncio
async def test_attached_exports_and_owner_pipeline_unharmed(otlp_server):
    reset_tracer_state()
    provider, collected = agent_style_provider()

    datasette_otel_otlp_exporter._install()
    state = datasette_otel_otlp_exporter._state
    assert state["owns_provider"] is False
    assert state["provider"] is provider

    await run_startup(endpoint=otlp_server.endpoint)
    assert state["mode"] == "active"

    emit_span("both-pipelines-see-this")
    provider.force_flush()

    assert "both-pipelines-see-this" in otlp_server.span_names()
    assert "both-pipelines-see-this" in {s.name for s in collected.get_finished_spans()}
    assert trace.get_tracer_provider() is provider


@pytest.mark.asyncio
async def test_dormant_attached_never_touches_sampler(otlp_server):
    "No endpoint + foreign provider: we discard; the owner keeps recording."
    reset_tracer_state()
    provider, collected = agent_style_provider()
    sampler_before = provider.sampler

    datasette_otel_otlp_exporter._install()
    await run_startup()  # no endpoint
    assert datasette_otel_otlp_exporter._state["mode"] == "dormant"

    assert provider.sampler is sampler_before
    emit_span("owner-still-records")
    provider.force_flush()

    assert "owner-still-records" in {s.name for s in collected.get_finished_spans()}
    assert otlp_server.requests == []


@pytest.mark.asyncio
async def test_dormant_owner_with_second_processor_keeps_sampling():
    "The starvation fix: dormant + a co-attached processor -> sampling stays on."
    reset_tracer_state()
    datasette_otel_otlp_exporter._install()
    state = datasette_otel_otlp_exporter._state
    assert state["owns_provider"] is True

    # Another exporter plugin attaches to our provider, parquet-style
    collected = InMemorySpanExporter()
    state["provider"].add_span_processor(SimpleSpanProcessor(collected))

    await run_startup()  # no endpoint: dormant
    assert state["mode"] == "dormant"
    assert state["sampler"]._delegate is not ALWAYS_OFF

    emit_span("co-attached-processor-sees-this")
    state["provider"].force_flush()
    assert "co-attached-processor-sees-this" in {
        s.name for s in collected.get_finished_spans()
    }


@pytest.mark.asyncio
async def test_dormant_sole_owner_still_stops_sampling():
    "The original optimization survives when no one else is attached."
    reset_tracer_state()
    datasette_otel_otlp_exporter._install()
    state = datasette_otel_otlp_exporter._state
    assert state["owns_provider"] is True

    await run_startup()  # no endpoint: dormant
    assert state["mode"] == "dormant"
    assert state["sampler"]._delegate is ALWAYS_OFF


@pytest.mark.asyncio
async def test_parquet_first_both_export(otlp_server, tmp_path):
    "The measured limitation from parquet's ticket 02, now fixed: either import order works."
    parquet_plugin = pytest.importorskip("datasette_otel_parquet")
    import glob

    reset_tracer_state()
    parquet_plugin._install()
    assert parquet_plugin._state["owns_provider"] is True

    datasette_otel_otlp_exporter._install()
    state = datasette_otel_otlp_exporter._state
    assert state["owns_provider"] is False
    assert state["provider"] is parquet_plugin._state["provider"]

    tel = tmp_path / "tel"
    datasette = Datasette(
        [],
        memory=True,
        config={
            "plugins": {
                "datasette-otel-otlp-exporter": {"endpoint": otlp_server.endpoint},
                "datasette-otel-parquet": {"path": str(tel)},
            }
        },
    )
    await datasette.invoke_startup()
    assert state["mode"] == "active"
    assert parquet_plugin._state["mode"] == "active"

    emit_span("either-order-works")
    parquet_plugin._state["provider"].force_flush()
    parquet_plugin._state["exporter"].force_flush()

    assert "either-order-works" in otlp_server.span_names()
    assert glob.glob(f"{tel}/traces/**/*.parquet", recursive=True)
