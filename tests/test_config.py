"""
PluginConfig: strict validation of the plugin's config block.
"""

import pytest
from datasette.app import Datasette

from datasette_otel_otlp_exporter.config import ConfigError, PluginConfig


def test_empty_config_is_valid():
    for raw in (None, {}):
        config = PluginConfig.parse(raw)
        assert config.endpoint is None
        assert config.headers == {}
        assert config.grafana_cloud is None


def test_full_config():
    config = PluginConfig.parse(
        {
            "endpoint": "http://localhost:4318",
            "headers": {"x-honeycomb-team": "key"},
        }
    )
    assert config.endpoint == "http://localhost:4318"
    assert config.headers == {"x-honeycomb-team": "key"}


def test_numbers_coerce_to_strings():
    "-s flags and YAML both turn numeric-looking values into numbers."
    config = PluginConfig.parse(
        {"endpoint": "http://localhost:4318", "headers": {"x-id": 42}}
    )
    assert config.headers == {"x-id": "42"}


@pytest.mark.parametrize(
    "raw,expected",
    [
        ({"sample_rate": 0.5}, "sample_rate: Extra inputs are not permitted"),
        # Removed in favor of OTEL_SERVICE_NAME / OTEL_TRACES_SAMPLER
        ({"service_name": "x"}, "service_name: Extra inputs are not permitted"),
        ({"sample_ratio": 0.5}, "sample_ratio: Extra inputs are not permitted"),
        ({"endpiont": "http://x"}, "endpiont: Extra inputs are not permitted"),
        ({"endpoint": "localhost:4318"}, "endpoint: must be an http:// or https://"),
        ({"endpoint": ""}, "endpoint: must be an http:// or https://"),
        ({"headers": "x-key: 1"}, "headers: Input should be a valid dictionary"),
        ({"headers": {"x-key": {"nested": 1}}}, "headers.x-key: Input should be"),
        ({"preset": "grafana-cloud"}, "preset: Extra inputs are not permitted"),
        (
            {
                "grafana_cloud": {
                    "instance_id": "1",
                    "api_token": "t",
                    "regoin": "prod-us-east-0",
                },
            },
            "grafana_cloud.regoin: Extra inputs are not permitted",
        ),
    ],
)
def test_invalid_config(raw, expected):
    with pytest.raises(ConfigError, match="invalid plugin config") as excinfo:
        PluginConfig.parse(raw)
    assert expected in str(excinfo.value)


def test_every_problem_is_reported():
    with pytest.raises(ConfigError) as excinfo:
        PluginConfig.parse({"sample_rate": 0.5, "endpoint": "nope"})
    message = str(excinfo.value)
    assert "sample_rate" in message
    assert "endpoint" in message


@pytest.mark.asyncio
async def test_typo_fails_startup():
    datasette = Datasette(
        memory=True,
        config={"plugins": {"datasette-otel-otlp-exporter": {"endpoitn": "x"}}},
    )
    with pytest.raises(ConfigError, match="endpoitn: Extra inputs"):
        await datasette.invoke_startup()
