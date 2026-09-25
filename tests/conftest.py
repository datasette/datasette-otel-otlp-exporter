"""
Test fixtures: an in-process OTLP/HTTP receiver, a lazy-exporter reset, and a
fresh-interpreter runner.

The plugin installs its provider once, at import. In this process that is
when conftest imports it, before any datasette span - so in-process tests
share one provider and only the lazy exporter needs rewinding between them.
Anything that depends on import order or on OTEL_* variables read at import
runs in a subprocess via run_python.
"""

import gzip
import os
import subprocess
import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)

import datasette_otel_otlp_exporter


@pytest.fixture(autouse=True)
def reset_exporter():
    "Drain the previous test's queued spans, then rearm as if startup never ran."
    exporter = datasette_otel_otlp_exporter._exporter
    exporter.configure(None)
    datasette_otel_otlp_exporter._processor.force_flush()
    with exporter._lock:
        exporter._configured = False
        exporter._pending = []
    yield
    # Stop exporting to this test's (soon gone) server
    exporter.configure(None)


def run_python(script, **env):
    """Run a script in a fresh interpreter with extra env vars set.

    Datasette's plugins (so this one) load when the script imports
    datasette.app. The SDK's atexit shutdown flushes every span on exit.
    """
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result


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
