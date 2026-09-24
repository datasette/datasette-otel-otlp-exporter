import pytest
from datasette.app import Datasette
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

import datasette_otel_otlp_exporter


def make_datasette(otlp_server=None, **plugin_settings):
    config = None
    if otlp_server is not None:
        plugin_settings.setdefault("endpoint", otlp_server.endpoint)
    if plugin_settings:
        config = {"plugins": {"datasette-otel-otlp-exporter": plugin_settings}}
    return Datasette(memory=True, config=config)


def flush():
    datasette_otel_otlp_exporter._state["provider"].force_flush()


@pytest.mark.asyncio
async def test_export_happens(otlp_server):
    datasette = make_datasette(otlp_server)
    response = await datasette.client.get("/")
    assert response.status_code == 200
    flush()

    names = otlp_server.span_names()
    assert any(name.startswith("GET ") for name in names)
    # The whole point of import-time wiring: the startup trace is captured
    assert "datasette.startup" in names
    db_spans = [s for s in otlp_server.spans if s["name"] == "db.query"]
    assert db_spans
    assert all(s["attributes"]["db.system"] == "sqlite" for s in db_spans)
    # Default service.name
    assert otlp_server.spans[0]["resource"]["service.name"] == "datasette"


@pytest.mark.asyncio
async def test_headers_reach_the_wire(otlp_server):
    datasette = make_datasette(otlp_server, headers={"x-test": "1"})
    await datasette.client.get("/")
    flush()

    assert otlp_server.requests
    assert all(r["headers"].get("x-test") == "1" for r in otlp_server.requests)


@pytest.mark.asyncio
async def test_headers_env_substitution(otlp_server, monkeypatch):
    "The README promises {'$env': ...} works for nested header values."
    monkeypatch.setenv("TEST_OTLP_SECRET", "sekrit")
    datasette = make_datasette(
        otlp_server, headers={"x-auth": {"$env": "TEST_OTLP_SECRET"}}
    )
    await datasette.client.get("/")
    flush()

    assert otlp_server.requests
    assert all(r["headers"].get("x-auth") == "sekrit" for r in otlp_server.requests)


@pytest.mark.asyncio
async def test_service_name_on_the_wire(otlp_server):
    datasette = make_datasette(otlp_server, service_name="my-datasette")
    await datasette.client.get("/")
    flush()

    assert otlp_server.spans
    assert all(
        s["resource"]["service.name"] == "my-datasette" for s in otlp_server.spans
    )
    # The retroactive resource swap covers the startup trace too
    assert "datasette.startup" in otlp_server.span_names()


@pytest.mark.asyncio
async def test_dormant_without_endpoint(otlp_server):
    datasette = make_datasette()
    response = await datasette.client.get("/")
    assert response.status_code == 200
    flush()

    assert datasette_otel_otlp_exporter._state["mode"] == "dormant"
    assert otlp_server.requests == []
    assert otlp_server.spans == []


@pytest.mark.asyncio
async def test_attaches_to_existing_sdk_provider(capsys):
    "A real SDK provider is joined, never replaced. Full flow in test_coexistence.py."
    from conftest import reset_tracer_state

    reset_tracer_state()
    mine = TracerProvider(shutdown_on_exit=False)
    trace.set_tracer_provider(mine)

    datasette_otel_otlp_exporter._install()
    assert datasette_otel_otlp_exporter._state["mode"] == "pending"
    assert datasette_otel_otlp_exporter._state["owns_provider"] is False
    assert trace.get_tracer_provider() is mine
    assert "attaching" in capsys.readouterr().err

    # The startup hook must not replace it either
    datasette = make_datasette()
    await datasette.client.get("/")
    assert trace.get_tracer_provider() is mine


@pytest.mark.asyncio
async def test_sample_ratio_zero(otlp_server):
    datasette = make_datasette(otlp_server, sample_ratio=0.0)
    # First request triggers invoke_startup; the startup trace was sampled
    # before config was readable, so flush it through and discard it
    await datasette.client.get("/")
    flush()
    otlp_server.clear()

    await datasette.client.get("/")
    flush()
    assert otlp_server.spans == []


@pytest.mark.asyncio
async def test_endpoint_path_appended(otlp_server):
    datasette = make_datasette(otlp_server)
    await datasette.client.get("/")
    flush()

    assert otlp_server.requests
    assert all(r["path"] == "/v1/traces" for r in otlp_server.requests)
