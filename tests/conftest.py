"""
Test fixtures: an in-process OTLP/HTTP receiver and an OpenTelemetry reset.

set_tracer_provider() is once-per-process in OpenTelemetry, and datasette's
telemetry module binds its tracer to whichever provider is global when the
first span resolves - importing this conftest installs the plugin's provider
before the test modules import datasette.app, so every datasette span in the
whole pytest run flows to that one provider. Rather than fight the once-only
semantics with a new provider per test, reset_otel keeps that single provider
and rewinds the plugin's mutable pieces (lazy exporter, deferred sampler,
resource attributes, state machine) between tests.
"""

import gzip
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from opentelemetry import trace
from opentelemetry.attributes import BoundedAttributes
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, DEFAULT_ON

import datasette_otel_otlp_exporter

# The provider the plugin installed when this module imported it, plus the
# original resource attributes - the baseline every test starts from.
_SNAPSHOT = dict(datasette_otel_otlp_exporter._state)
_SNAPSHOT_RESOURCE_ATTRIBUTES = dict(_SNAPSHOT["resource"].attributes)


def reset_tracer_state():
    "Unwind OpenTelemetry's set-once provider global (for foreign-provider tests)."
    trace._TRACER_PROVIDER = None
    trace._TRACER_PROVIDER_SET_ONCE._done = False


def _quiesce():
    "Point the lazy exporter at nothing and stop sampling new spans."
    exporter = _SNAPSHOT["exporter"]
    exporter.configure(None)
    if _SNAPSHOT["sampler"] is not None:
        _SNAPSHOT["sampler"].set_delegate(ALWAYS_OFF)


@pytest.fixture(autouse=True)
def reset_otel():
    # If the previous test replaced the global provider (the foreign-provider
    # test does), point the world back at the plugin's own
    trace._TRACER_PROVIDER = _SNAPSHOT["provider"]
    trace._TRACER_PROVIDER_SET_ONCE._done = True
    state = datasette_otel_otlp_exporter._state
    state.clear()
    state.update(_SNAPSHOT)
    state["mode"] = "pending"
    state["dormant_logged"] = False

    # Drain spans left queued by the previous test into a discarding exporter,
    # then rearm the lazy exporter as if the startup hook had never run
    exporter = state["exporter"]
    exporter.configure(None)
    state["provider"].force_flush()
    with exporter._lock:
        exporter._configured = False
        exporter._delegate = None
        exporter._pending = []

    if state["sampler"] is not None:
        state["sampler"].set_delegate(DEFAULT_ON)
    state["resource"]._attributes = BoundedAttributes(
        attributes=_SNAPSHOT_RESOURCE_ATTRIBUTES, immutable=True
    )
    yield
    # Quiesce so nothing tries to POST to this test's (now gone) server
    _quiesce()


def _attribute_value(value):
    for field in ("string_value", "int_value", "double_value", "bool_value"):
        if value.HasField(field):
            return getattr(value, field)
    return None


class OTLPServer:
    "Collects every span and request the OTLP exporter sends."

    def __init__(self, server, port):
        self._server = server
        self.port = port
        self.spans = []
        self.requests = []

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.port}"

    def clear(self):
        self.spans.clear()
        self.requests.clear()

    def span_names(self):
        return [span["name"] for span in self.spans]


@pytest.fixture
def otlp_server():
    collected_spans = []
    collected_requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if self.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            collected_requests.append(
                {"path": self.path, "headers": dict(self.headers)}
            )

            request = ExportTraceServiceRequest()
            request.ParseFromString(body)
            for resource_spans in request.resource_spans:
                resource = {
                    a.key: _attribute_value(a.value)
                    for a in resource_spans.resource.attributes
                }
                for scope_spans in resource_spans.scope_spans:
                    for span in scope_spans.spans:
                        collected_spans.append(
                            {
                                "name": span.name,
                                "resource": resource,
                                "attributes": {
                                    a.key: _attribute_value(a.value)
                                    for a in span.attributes
                                },
                            }
                        )

            payload = ExportTraceServiceResponse().SerializeToString()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    wrapper = OTLPServer(server, server.server_address[1])
    wrapper.spans = collected_spans
    wrapper.requests = collected_requests
    yield wrapper
    server.shutdown()
