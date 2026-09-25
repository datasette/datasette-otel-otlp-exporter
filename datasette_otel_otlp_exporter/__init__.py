"""
Wire Datasette's OpenTelemetry spans to an OTLP/HTTP backend.

Datasette core emits spans through the OpenTelemetry API but never installs a
TracerProvider, so without help every span is a NonRecordingSpan. The
provider is installed at import, since a span started before one exists is
non-recording forever and ``datasette.startup`` (and the spans that end
inside it) runs before any plugin hook. Its sampler and ``service.name`` come
from the standard ``OTEL_TRACES_SAMPLER``/``OTEL_SERVICE_NAME`` environment
variables (service name default "datasette").

Export needs plugin config, which is only readable in the ``startup()`` hook,
so the BatchSpanProcessor wraps a lazy exporter that the hook points at a
real OTLP exporter - or, with no endpoint configured, at nothing.

If a real SDK ``TracerProvider`` is already installed (the
``opentelemetry-instrument`` agent, or another exporter plugin imported
first), the processor is attached to it instead and the owner's sampler and
``service.name`` apply. ``OTEL_EXPORTER_OTLP_*`` environment variables beat
plugin config.
"""

import base64
import os
import sys
import threading
from typing import Any
from urllib.parse import urlparse

from datasette import hookimpl
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

from .config import PLUGIN_NAME, GrafanaCloudOptions, PluginConfig

DEFAULT_SERVICE_NAME = "datasette"

_ENDPOINT_ENV_VARS = (
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
)
_HEADERS_ENV_VARS = (
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    "OTEL_EXPORTER_OTLP_HEADERS",
)


class _LazySpanExporter(SpanExporter):
    """Stands in for the OTLP exporter until plugin config is readable.

    The BatchSpanProcessor queues finished spans and only calls export()
    seconds after startup, so normally configure() has already run by the
    first export. If an export does arrive earlier, the spans are held here
    (bounded) and forwarded on configure().
    """

    _PENDING_LIMIT = 4096

    def __init__(self):
        self._lock = threading.Lock()
        self._configured = False
        self._delegate = None
        self._pending = []

    def configure(self, delegate):
        "delegate=None means dormant: drop everything, now and from now on."
        with self._lock:
            previous = self._delegate
            self._delegate = delegate
            self._configured = True
            pending, self._pending = self._pending, []
        if previous is not None and previous is not delegate:
            previous.shutdown()
        if delegate is not None and pending:
            delegate.export(pending)

    def export(self, spans):
        with self._lock:
            if not self._configured:
                if len(self._pending) < self._PENDING_LIMIT:
                    self._pending.extend(spans)
                return SpanExportResult.SUCCESS
            delegate = self._delegate
        if delegate is None:
            return SpanExportResult.SUCCESS
        return delegate.export(spans)

    def shutdown(self):
        with self._lock:
            delegate = self._delegate
        if delegate is not None:
            delegate.shutdown()

    def force_flush(self, timeout_millis=30000):
        with self._lock:
            delegate = self._delegate
        if delegate is not None:
            return delegate.force_flush(timeout_millis)
        return True


def _log(message):
    print(f"{PLUGIN_NAME}: {message}", file=sys.stderr)


_exporter = _LazySpanExporter()
_processor = BatchSpanProcessor(_exporter)

_existing = trace.get_tracer_provider()
# Only the Proxy means "unset": a NoOpTracerProvider was set deliberately,
# and set_tracer_provider() refuses to replace it.
if isinstance(_existing, trace.ProxyTracerProvider):
    # Honour OTEL_SERVICE_NAME / OTEL_RESOURCE_ATTRIBUTES, and only fall back
    # to "datasette" over the SDK's "unknown_service" placeholder
    _resource = Resource.create()
    if str(_resource.attributes.get("service.name", "")).startswith("unknown_service"):
        _resource = _resource.merge(Resource({"service.name": DEFAULT_SERVICE_NAME}))
    # No sampler argument: the SDK builds one from OTEL_TRACES_SAMPLER
    _provider = TracerProvider(resource=_resource)
    _provider.add_span_processor(_processor)
    trace.set_tracer_provider(_provider)
elif isinstance(_existing, TracerProvider):
    # Someone else owns the provider. Join it: our processor sees every span
    # that ends from here on - including the whole startup trace.
    _existing.add_span_processor(_processor)
    _log("attaching to the already-installed TracerProvider")
else:
    _log(
        "a non-SDK TracerProvider is installed; cannot attach a span "
        "processor - OTLP export is disabled"
    )


def _normalize_endpoint(endpoint):
    "Append /v1/traces when the configured endpoint has no path."
    parsed = urlparse(endpoint)
    if parsed.path in ("", "/"):
        return endpoint.rstrip("/") + "/v1/traces"
    return endpoint


_GRAFANA_CLOUD_ENDPOINT = "https://otlp-gateway-{region}.grafana.net/otlp/v1/traces"


def _resolve_grafana_cloud(options: GrafanaCloudOptions):
    """Endpoint + basic-auth header for Grafana Cloud's OTLP gateway.

    The gateway wants the full per-signal path (the SDK only appends
    /v1/traces to bare env-var endpoints, and _normalize_endpoint only to
    path-less ones) and HTTP basic auth with the stack/instance id as
    username and a grafana.com token as password.
    """
    endpoint = options.endpoint or _GRAFANA_CLOUD_ENDPOINT.format(region=options.region)
    credentials = f"{options.instance_id}:{options.api_token.get_secret_value()}"
    token = base64.b64encode(credentials.encode()).decode()
    return endpoint, {"Authorization": f"Basic {token}"}


def _resolve_preset(config: PluginConfig):
    """The (endpoint, headers) defaults contributed by a preset block, if any.

    A preset is pure sugar: explicit ``endpoint``/``headers`` config beats
    its values, and OTEL_* env vars beat both.
    """
    if config.grafana_cloud is not None:
        return _resolve_grafana_cloud(config.grafana_cloud)
    return None, None


def _configure(raw_config):
    "Called from the startup() hook with plugin config."
    # Validate before the env checks: a typo'd key or preset block should
    # fail loudly even when env vars would override it.
    config = PluginConfig.parse(raw_config)
    preset_endpoint, preset_headers = _resolve_preset(config)

    env_endpoint = any(var in os.environ for var in _ENDPOINT_ENV_VARS)
    env_headers = any(var in os.environ for var in _HEADERS_ENV_VARS)
    endpoint = config.endpoint or preset_endpoint

    if not env_endpoint and not endpoint:
        _exporter.configure(None)
        _log("no endpoint configured - OpenTelemetry export is disabled")
        return

    exporter_kwargs: dict[str, Any] = {}
    if not env_endpoint:
        exporter_kwargs["endpoint"] = _normalize_endpoint(endpoint)
    if not env_headers:
        # Preset headers first, explicit headers merged over them so an
        # operator can add extras (or replace the auth header) per key.
        headers = {**(preset_headers or {}), **config.headers}
        if headers:
            exporter_kwargs["headers"] = headers
    _exporter.configure(OTLPSpanExporter(**exporter_kwargs))


@hookimpl
def startup(datasette):
    _configure(datasette.plugin_config(PLUGIN_NAME))
