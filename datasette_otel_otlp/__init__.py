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
   resolve endpoint/headers/service_name/sample_ratio and point the lazy
   exporter at a real OTLP exporter - or, with no endpoint configured, drop
   everything and sample nothing from then on.

Precedence: explicit ``OTEL_*`` environment variables beat plugin config, and
if some other machinery (the ``opentelemetry-instrument`` agent) already
installed a provider, this plugin announces itself once on stderr and does
nothing else.

Config settled at import time uses defaults; the startup hook retrofits what
it can. Concretely: the startup trace's ``service.name`` is fixed up
retroactively (spans hold the provider's Resource by reference), but its
sampling decision is not - spans started before config is read are sampled at
the default (always on), so ``sample_ratio`` applies to requests, not to the
startup trace.
"""

import os
import sys
import threading
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

PLUGIN_NAME = "datasette-otel-otlp"
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
#   "foreign"  - someone else installed a provider; we do nothing
#   "pending"  - our provider is installed, waiting for the startup hook
#   "active"   - exporting
#   "dormant"  - no endpoint anywhere; recording is switched off
_state = {}


def _log(message):
    print(f"{PLUGIN_NAME}: {message}", file=sys.stderr)


def _install():
    "Runs at module import; also re-runnable by tests after resetting otel globals."
    _state.clear()
    existing = trace.get_tracer_provider()
    if not isinstance(
        existing, (trace.ProxyTracerProvider, trace.NoOpTracerProvider)
    ):
        _log(
            "a TracerProvider is already installed "
            "(running under opentelemetry-instrument?) - leaving it alone"
        )
        _state["mode"] = "foreign"
        return

    resource_attributes = {}
    if "OTEL_SERVICE_NAME" not in os.environ:
        resource_attributes["service.name"] = DEFAULT_SERVICE_NAME
    resource = Resource.create(resource_attributes)

    # If OTEL_TRACES_SAMPLER is set, let the SDK build the sampler from the
    # environment (env beats plugin config); otherwise install a delegating
    # sampler the startup hook can retarget.
    sampler = None if "OTEL_TRACES_SAMPLER" in os.environ else _DeferredSampler()

    exporter = _LazySpanExporter()
    if sampler is not None:
        provider = TracerProvider(sampler=sampler, resource=resource)
    else:
        provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    _state.update(
        mode="pending",
        provider=provider,
        resource=resource,
        sampler=sampler,
        exporter=exporter,
        dormant_logged=False,
    )


def _normalize_endpoint(endpoint):
    "Append /v1/traces when the configured endpoint has no path."
    parsed = urlparse(endpoint)
    if parsed.path in ("", "/"):
        return endpoint.rstrip("/") + "/v1/traces"
    return endpoint


def _set_service_name(resource, service_name):
    # Resource is immutable by design, but every span holds this exact object
    # by reference - including the still-open datasette.startup span - so
    # replacing its attribute mapping retrofits the name onto everything not
    # yet exported.
    attributes = dict(resource.attributes)
    attributes["service.name"] = service_name
    resource._attributes = BoundedAttributes(attributes=attributes, immutable=True)


def _configure(config):
    "Second phase: called from the startup() hook with plugin config."
    if _state.get("mode") == "foreign":
        return

    if (
        config.get("service_name")
        and "OTEL_SERVICE_NAME" not in os.environ
    ):
        _set_service_name(_state["resource"], str(config["service_name"]))

    env_endpoint = any(var in os.environ for var in _ENDPOINT_ENV_VARS)
    env_headers = any(var in os.environ for var in _HEADERS_ENV_VARS)
    endpoint = config.get("endpoint")

    if not env_endpoint and not endpoint:
        # Dormant: no export target anywhere. Stop recording spans too, so an
        # unconfigured install costs as close to nothing as an installed SDK
        # provider can.
        if _state["sampler"] is not None:
            _state["sampler"].set_delegate(ALWAYS_OFF)
        _state["exporter"].configure(None)
        if not _state["dormant_logged"]:
            _log("no endpoint configured - OpenTelemetry export is disabled")
            _state["dormant_logged"] = True
        _state["mode"] = "dormant"
        return

    if _state["sampler"] is not None and "sample_ratio" in config:
        ratio = float(config["sample_ratio"])
        _state["sampler"].set_delegate(ParentBased(TraceIdRatioBased(ratio)))

    exporter_kwargs = {}
    if not env_endpoint:
        exporter_kwargs["endpoint"] = _normalize_endpoint(str(endpoint))
    if not env_headers and config.get("headers"):
        exporter_kwargs["headers"] = {
            str(key): str(value) for key, value in config["headers"].items()
        }
    _state["exporter"].configure(OTLPSpanExporter(**exporter_kwargs))
    _state["mode"] = "active"


_install()


@hookimpl
def startup(datasette):
    _configure(datasette.plugin_config(PLUGIN_NAME) or {})
