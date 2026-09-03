"""
Ticket 07: the grafana-cloud preset.

The preset never sees the network in tests - Grafana Cloud is asserted
against the resolved OTLPSpanExporter (its _endpoint/_headers), reached
through the lazy exporter's delegate after the startup hook runs.
"""

import base64

import pytest
from datasette.app import Datasette

import datasette_otel_otlp_exporter
from datasette_otel_otlp_exporter import _resolve_preset

GRAFANA_ENDPOINT = "https://otlp-gateway-prod-us-east-0.grafana.net/otlp/v1/traces"
BASIC_AUTH = "Basic " + base64.b64encode(b"123456:glc_secret").decode()


def make_datasette(**plugin_settings):
    return Datasette(
        memory=True,
        config={"plugins": {"datasette-otel-otlp-exporter": plugin_settings}},
    )


def grafana_settings(**overrides):
    settings = {
        "preset": "grafana-cloud",
        "grafana_cloud": {
            # ints on purpose: -s flags and YAML can both produce non-strings
            "instance_id": 123456,
            "api_token": "glc_secret",
            "region": "prod-us-east-0",
        },
    }
    settings.update(overrides)
    return settings


def delegate():
    return datasette_otel_otlp_exporter._state["exporter"]._delegate


# --- unit: the resolver ---


def test_resolver_region_and_auth():
    endpoint, headers = _resolve_preset(grafana_settings())
    assert endpoint == GRAFANA_ENDPOINT
    assert headers == {"Authorization": BASIC_AUTH}


def test_resolver_endpoint_override_in_options():
    settings = grafana_settings()
    del settings["grafana_cloud"]["region"]
    settings["grafana_cloud"]["endpoint"] = "https://otlp.example.com/otlp/v1/traces"
    endpoint, _ = _resolve_preset(settings)
    assert endpoint == "https://otlp.example.com/otlp/v1/traces"


def test_resolver_no_preset_is_none():
    assert _resolve_preset({"endpoint": "http://localhost:4318"}) == (None, None)


def test_resolver_unknown_preset():
    with pytest.raises(ValueError, match="unknown preset 'honeycomb'"):
        _resolve_preset({"preset": "honeycomb"})


def test_resolver_missing_fields_are_named():
    with pytest.raises(ValueError) as excinfo:
        _resolve_preset({"preset": "grafana-cloud", "grafana_cloud": {}})
    message = str(excinfo.value)
    assert "region (or endpoint)" in message
    assert "instance_id" in message
    assert "api_token" in message


# --- integration: through the startup hook ---


@pytest.mark.asyncio
async def test_preset_configures_the_exporter():
    datasette = make_datasette(**grafana_settings())
    await datasette.invoke_startup()

    assert datasette_otel_otlp_exporter._state["mode"] == "active"
    assert delegate()._endpoint == GRAFANA_ENDPOINT
    assert delegate()._headers.get("Authorization") == BASIC_AUTH


@pytest.mark.asyncio
async def test_explicit_endpoint_beats_preset_headers_survive():
    datasette = make_datasette(
        **grafana_settings(endpoint="http://localhost:4318")
    )
    await datasette.invoke_startup()

    assert delegate()._endpoint == "http://localhost:4318/v1/traces"
    assert delegate()._headers.get("Authorization") == BASIC_AUTH


@pytest.mark.asyncio
async def test_explicit_headers_merge_over_preset():
    datasette = make_datasette(
        **grafana_settings(
            headers={"x-extra": "1", "Authorization": "Bearer mine"}
        )
    )
    await datasette.invoke_startup()

    assert delegate()._headers.get("x-extra") == "1"
    assert delegate()._headers.get("Authorization") == "Bearer mine"


@pytest.mark.asyncio
async def test_env_endpoint_beats_preset(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    datasette = make_datasette(**grafana_settings())
    await datasette.invoke_startup()

    assert "grafana.net" not in delegate()._endpoint


@pytest.mark.asyncio
async def test_unknown_preset_fails_startup():
    datasette = make_datasette(preset="nonesuch")
    with pytest.raises(ValueError, match="unknown preset"):
        await datasette.invoke_startup()


@pytest.mark.asyncio
async def test_missing_fields_fail_startup_even_with_env_endpoint(monkeypatch):
    "A typo'd preset block is misconfiguration regardless of env overrides."
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    datasette = make_datasette(preset="grafana-cloud")
    with pytest.raises(ValueError, match="grafana-cloud preset needs"):
        await datasette.invoke_startup()
