"""
Import-time wiring, each case in a fresh interpreter: who owns the provider,
and the OTEL_* variables the provider reads when it is built.

The scripts serve one request through Datasette, which loads the plugin, and
exit; the SDK's atexit shutdown then flushes every span to otlp_server.
"""

import pytest
from conftest import run_python

REQUEST = """
    import asyncio, os
    from datasette.app import Datasette

    async def main():
        plugins = {"datasette-otel-otlp-exporter": {"endpoint": os.environ["ENDPOINT"]}}
        await Datasette(memory=True, config={"plugins": plugins}).client.get("/")

    asyncio.run(main())
"""

AGENT_PROVIDER = """
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    collected = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(collected))
    trace.set_tracer_provider(provider)
"""


def test_installs_own_provider(otlp_server):
    run_python(
        REQUEST
        + """
    from opentelemetry import trace
    import datasette_otel_otlp_exporter as plugin
    assert trace.get_tracer_provider() is plugin._provider
    """,
        ENDPOINT=otlp_server.endpoint,
    )
    assert "datasette.startup" in otlp_server.span_names()
    assert otlp_server.spans[0]["resource"]["service.name"] == "datasette"


def test_otel_service_name(otlp_server):
    run_python(REQUEST, ENDPOINT=otlp_server.endpoint, OTEL_SERVICE_NAME="my-ds")
    assert otlp_server.spans
    assert all(s["resource"]["service.name"] == "my-ds" for s in otlp_server.spans)


def test_otel_resource_attributes_service_name(otlp_server):
    run_python(
        REQUEST,
        ENDPOINT=otlp_server.endpoint,
        OTEL_RESOURCE_ATTRIBUTES="service.name=from-attrs",
    )
    assert otlp_server.spans
    assert all(s["resource"]["service.name"] == "from-attrs" for s in otlp_server.spans)


def test_otel_traces_sampler(otlp_server):
    "Read at import, so it covers the startup trace too."
    run_python(
        REQUEST,
        ENDPOINT=otlp_server.endpoint,
        OTEL_TRACES_SAMPLER="parentbased_traceidratio",
        OTEL_TRACES_SAMPLER_ARG="0",
    )
    assert otlp_server.spans == []


def test_attaches_to_existing_sdk_provider(otlp_server):
    "Joined, never replaced: both pipelines see every span."
    result = run_python(
        AGENT_PROVIDER
        + REQUEST
        + """
    assert trace.get_tracer_provider() is provider
    provider.force_flush()
    assert "datasette.startup" in {s.name for s in collected.get_finished_spans()}
    """,
        ENDPOINT=otlp_server.endpoint,
    )
    assert "attaching" in result.stderr
    assert "datasette.startup" in otlp_server.span_names()


def test_attached_dormant_leaves_owner_recording(otlp_server):
    "No endpoint: our copies are dropped, the owner's pipeline is unaffected."
    run_python(
        AGENT_PROVIDER
        + """
    import asyncio
    from datasette.app import Datasette

    asyncio.run(Datasette(memory=True).client.get("/"))
    provider.force_flush()
    assert "datasette.startup" in {s.name for s in collected.get_finished_spans()}
    """
    )
    assert otlp_server.requests == []


def test_non_sdk_provider_is_left_alone(otlp_server):
    result = run_python(
        """
    from opentelemetry import trace
    trace.set_tracer_provider(trace.NoOpTracerProvider())
    """
        + REQUEST,
        ENDPOINT=otlp_server.endpoint,
    )
    assert "non-SDK TracerProvider" in result.stderr
    assert otlp_server.requests == []


def test_file_exporter_first_both_export(otlp_server, tmp_path):
    "Another exporter plugin owns the provider; either import order works."
    pytest.importorskip("datasette_otel_file_exporter")
    result = run_python(
        """
    import asyncio, os
    import datasette_otel_file_exporter
    from datasette.app import Datasette

    plugins = {
        "datasette-otel-otlp-exporter": {"endpoint": os.environ["ENDPOINT"]},
        "datasette-otel-file-exporter": {"path": os.environ["TEL"]},
    }
    asyncio.run(Datasette(memory=True, config={"plugins": plugins}).client.get("/"))
    """,
        ENDPOINT=otlp_server.endpoint,
        TEL=str(tmp_path / "tel"),
    )
    assert "attaching" in result.stderr
    assert "datasette.startup" in otlp_server.span_names()
    assert [p for p in (tmp_path / "tel").rglob("*") if p.is_file()]
