"""
Wire Datasette's OpenTelemetry spans to an OTLP/HTTP backend.

Datasette core emits spans through the OpenTelemetry API but never installs a
TracerProvider, so without help every span is a NonRecordingSpan. This plugin
installs the provider in two phases:

1. At module import (plugins load before ``invoke_startup()``), install an SDK
   ``TracerProvider`` with a ``BatchSpanProcessor`` wrapped around a lazy
   exporter. This must happen at import: a span started through the API's
   ProxyTracer before a provider exists is non-recording forever, and the
   ``datasette.startup`` span starts before any plugin hook runs.

2. In the ``startup()`` hook, the first place plugin config is readable,
   resolve endpoint/headers/service_name/sample_ratio (a preset block
   such as ``grafana_cloud`` is sugar that contributes endpoint + auth
   header defaults) and point the lazy exporter at a real OTLP exporter - or, with
   no endpoint configured, drop everything and sample nothing from then on.

Precedence: explicit ``OTEL_*`` environment variables beat plugin config.
When a real SDK ``TracerProvider`` is already installed (the
``opentelemetry-instrument`` agent, or another exporter plugin such as
datasette-otel-parquet imported first - entry-point import order is not
guaranteed), this plugin does not step aside: it attaches its processor to
that provider with ``add_span_processor()``, so export still happens. In
that attached mode the owner's sampler and ``service.name`` apply, and the
``sample_ratio``/``service_name`` config keys are ignored.

Config settled at import time uses defaults; the startup hook retrofits what
it can. Concretely: the startup trace's ``service.name`` is fixed up
retroactively (spans hold the provider's Resource by reference), but its
sampling decision is not - spans started before config is read are sampled at
the default (always on), so ``sample_ratio`` applies to requests, not to the
startup trace.
"""

import base64
import os
import sys
import threading
from typing import Any
from urllib.parse import urlparse

from datasette import hookimpl
from opentelemetry import trace
from opentelemetry.attributes import BoundedAttributes
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_OFF,
    DEFAULT_ON,
    ParentBased,
    Sampler,
    TraceIdRatioBased,
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


class _DeferredSampler(Sampler):
    "ParentBased(ALWAYS_ON) until the startup hook swaps in the configured one."

    def __init__(self):
        self._delegate = DEFAULT_ON

    def set_delegate(self, sampler):
        self._delegate = sampler

    def should_sample(self, *args, **kwargs):
        return self._delegate.should_sample(*args, **kwargs)

    def get_description(self):
        return f"{PLUGIN_NAME}({self._delegate.get_description()})"


# Module state, rebuilt by _install(). "mode" is one of:
#   "pending"  - processor is wired (own or attached provider), waiting for
#                the startup hook
#   "active"   - exporting
#   "dormant"  - no endpoint anywhere; recording is off (when we own the
#                provider and nothing else is attached to it) or the
#                processor just discards (otherwise)
#   "inert"    - a non-SDK provider we cannot attach to; we do nothing
# "owns_provider" records which of install/attach happened.
_state = {}


def _log(message):
    print(f"{PLUGIN_NAME}: {message}", file=sys.stderr)


def _install():
    "Runs at module import; also re-runnable by tests after resetting otel globals."
    _state.clear()
    existing = trace.get_tracer_provider()

    if isinstance(existing, (trace.ProxyTracerProvider, trace.NoOpTracerProvider)):
        # No one owns tracing yet: install our own provider (two-phase).
        resource_attributes = {}
        if "OTEL_SERVICE_NAME" not in os.environ:
            resource_attributes["service.name"] = DEFAULT_SERVICE_NAME
        resource = Resource.create(resource_attributes)

        # If OTEL_TRACES_SAMPLER is set, let the SDK build the sampler from
        # the environment (env beats plugin config); otherwise install a
        # delegating sampler the startup hook can retarget.
        sampler = None if "OTEL_TRACES_SAMPLER" in os.environ else _DeferredSampler()

        exporter = _LazySpanExporter()
        if sampler is not None:
            provider = TracerProvider(sampler=sampler, resource=resource)
        else:
            provider = TracerProvider(resource=resource)
        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)

        _state.update(
            mode="pending",
            owns_provider=True,
            provider=provider,
            processor=processor,
            resource=resource,
            sampler=sampler,
            exporter=exporter,
            dormant_logged=False,
        )
        return

    if isinstance(existing, TracerProvider):
        # Someone else (the agent, or another exporter plugin imported
        # first) owns the provider. Join it: their sampler and service.name
        # apply, our processor sees every span that ends from here on -
        # including the whole startup trace, which has not ended yet.
        exporter = _LazySpanExporter()
        processor = BatchSpanProcessor(exporter)
        existing.add_span_processor(processor)
        _log("attaching to the already-installed TracerProvider")
        _state.update(
            mode="pending",
            owns_provider=False,
            provider=existing,
            processor=processor,
            resource=None,
            sampler=None,
            exporter=exporter,
            dormant_logged=False,
        )
        return

    _log(
        "a non-SDK TracerProvider is installed; cannot attach a span "
        "processor - OTLP export is disabled"
    )
    _state["mode"] = "inert"


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


def _set_service_name(resource, service_name):
    # Resource is immutable by design, but every span holds this exact object
    # by reference - including the still-open datasette.startup span - so
    # replacing its attribute mapping retrofits the name onto everything not
    # yet exported.
    attributes = dict(resource.attributes)
    attributes["service.name"] = service_name
    resource._attributes = BoundedAttributes(attributes=attributes, immutable=True)


def _is_sole_processor(provider, processor):
    """Best-effort: is our processor the only one attached to the provider?

    Switching the sampler to ALWAYS_OFF when dormant is a pure optimization
    for the unconfigured-install case - but it silences every OTHER
    processor on the provider too (measured: datasette-otel-parquet
    attached to a dormant otlp-owned provider recorded nothing). Peeking at
    the multi-processor is version-dependent private API; when the answer
    is unknowable, report False so sampling stays on - correctness beats
    the optimization.
    """
    try:
        return provider._active_span_processor._span_processors == (processor,)
    except AttributeError:
        return False


def _configure(raw_config):
    "Second phase: called from the startup() hook with plugin config."
    # Validate before anything else: a typo'd key or preset block should fail
    # loudly even when env vars would override it or we are inert.
    config = PluginConfig.parse(raw_config)
    if _state.get("mode") == "inert":
        return

    preset_endpoint, preset_headers = _resolve_preset(config)

    owns = _state["owns_provider"]
    if owns and config.service_name and "OTEL_SERVICE_NAME" not in os.environ:
        _set_service_name(_state["resource"], config.service_name)

    env_endpoint = any(var in os.environ for var in _ENDPOINT_ENV_VARS)
    env_headers = any(var in os.environ for var in _HEADERS_ENV_VARS)
    endpoint = config.endpoint or preset_endpoint

    if not env_endpoint and not endpoint:
        # Dormant: no export target anywhere. When we own the provider and
        # ours is the only processor on it, stop recording spans too, so an
        # unconfigured install costs as close to nothing as an installed SDK
        # provider can. When another processor is attached (or the provider
        # is someone else's), the sampler is not ours to switch off - just
        # discard our own copies.
        if (
            owns
            and _state["sampler"] is not None
            and _is_sole_processor(_state["provider"], _state["processor"])
        ):
            _state["sampler"].set_delegate(ALWAYS_OFF)
        _state["exporter"].configure(None)
        if not _state["dormant_logged"]:
            _log("no endpoint configured - OpenTelemetry export is disabled")
            _state["dormant_logged"] = True
        _state["mode"] = "dormant"
        return

    if _state["sampler"] is not None and config.sample_ratio is not None:
        _state["sampler"].set_delegate(
            ParentBased(TraceIdRatioBased(config.sample_ratio))
        )

    exporter_kwargs: dict[str, Any] = {}
    if not env_endpoint:
        exporter_kwargs["endpoint"] = _normalize_endpoint(endpoint)
    if not env_headers:
        # Preset headers first, explicit headers merged over them so an
        # operator can add extras (or replace the auth header) per key.
        headers = {**(preset_headers or {}), **config.headers}
        if headers:
            exporter_kwargs["headers"] = headers
    _state["exporter"].configure(OTLPSpanExporter(**exporter_kwargs))
    _state["mode"] = "active"


_install()


@hookimpl
def startup(datasette):
    _configure(datasette.plugin_config(PLUGIN_NAME))
