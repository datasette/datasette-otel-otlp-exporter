"Validated plugin config: unknown keys and bad values fail startup."

from typing import Annotated
from urllib.parse import urlparse

from datasette.utils import StartupError
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    model_validator,
)

PLUGIN_NAME = "datasette-otel-otlp-exporter"


class ConfigError(StartupError):
    "Plugin config failed validation; the message lists every problem."


def _check_http_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("must be an http:// or https:// URL")
    return value


HttpUrlStr = Annotated[str, AfterValidator(_check_http_url)]


class _Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        coerce_numbers_to_str=True,
    )


class GrafanaCloudOptions(_Model):
    """The ``grafana_cloud`` block: export to Grafana Cloud's OTLP gateway.

    All values come from your stack's **OpenTelemetry** tile in the Grafana
    Cloud portal: https://grafana.com/docs/grafana-cloud/send-data/otlp/send-data-otlp/
    """

    instance_id: str = Field(min_length=1)
    """Your stack's numeric instance ID, sent as the basic-auth username.

    Shown as "Instance ID" on the OpenTelemetry tile:
    https://grafana.com/docs/grafana-cloud/send-data/otlp/send-data-otlp/
    """

    api_token: SecretStr = Field(min_length=1)
    """An access policy token with the ``traces:write`` scope, sent as the
    basic-auth password.

    https://grafana.com/docs/grafana-cloud/send-data/traces/set-up/add-access-policy/
    https://grafana.com/docs/grafana-cloud/security-and-account-management/authentication-and-permissions/access-policies/create-access-policies/
    """

    region: str | None = Field(default=None, min_length=1)
    """The stack's region, such as ``prod-us-east-0``. Builds the endpoint
    ``https://otlp-gateway-<region>.grafana.net/otlp/v1/traces``.

    That host shape only holds for older regions; newer ones differ, so set
    ``endpoint`` instead if it doesn't match the tile:
    https://grafana.com/docs/grafana-cloud/platform/security-and-account-management/account-management/region-url-formats/
    """

    endpoint: HttpUrlStr | None = None
    """The full OTLP traces URL, ending in ``/otlp/v1/traces``. Overrides
    ``region``; copy the host from the OpenTelemetry tile:
    https://grafana.com/docs/grafana-cloud/send-data/otlp/send-data-otlp/
    """

    @model_validator(mode="after")
    def _region_or_endpoint(self):
        if self.region is None and self.endpoint is None:
            raise ValueError("needs region (or endpoint)")
        return self


class PluginConfig(_Model):
    "Everything under ``plugins.datasette-otel-otlp-exporter``."

    endpoint: HttpUrlStr | None = None
    """Base OTLP/HTTP URL, e.g. ``http://localhost:4318``. ``/v1/traces`` is
    appended when it has no path. Unset (and no preset) means dormant.
    ``OTEL_EXPORTER_OTLP_[TRACES_]ENDPOINT`` overrides it."""

    headers: dict[str, str] = Field(default_factory=dict)
    """HTTP headers sent with every export, e.g. vendor API keys. Merged over
    any preset headers. ``OTEL_EXPORTER_OTLP_[TRACES_]HEADERS`` overrides it."""

    grafana_cloud: GrafanaCloudOptions | None = None
    """Grafana Cloud preset: when present, supplies a default ``endpoint`` and
    basic-auth ``headers``, both of which explicit config overrides."""

    @classmethod
    def parse(cls, raw) -> "PluginConfig":
        "Validate raw plugin config, raising ConfigError with a readable message."
        try:
            return cls.model_validate(raw or {})
        except ValidationError as error:
            problems = []
            for item in error.errors():
                location = ".".join(str(part) for part in item["loc"])
                message = item["msg"].removeprefix("Value error, ")
                problems.append(f"  {location or '(config)'}: {message}")
            raise ConfigError(
                f"{PLUGIN_NAME}: invalid plugin config\n" + "\n".join(problems)
            ) from None
