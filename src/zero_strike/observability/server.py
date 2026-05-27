"""Stdlib HTTP server for /metrics + /healthz. Runs in a background thread."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .metrics import collect_metrics, render_prometheus


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/metrics", "/metrics/"):
            try:
                body = render_prometheus(collect_metrics()).encode()
            except Exception as e:
                self._respond(500, f"collection_error: {e}".encode(), "text/plain")
                return
            self._respond(200, body, "text/plain; version=0.0.4; charset=utf-8")
        elif self.path in ("/healthz", "/healthz/"):
            self._respond(200, b"ok\n", "text/plain")
        else:
            self._respond(404, b"not found\n", "text/plain")

    def _respond(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args, **kwargs):
        return  # silence default access log


def start_metrics_server(host: str = "0.0.0.0", port: int = 9090) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True, name="zs-metrics")
    t.start()
    return server
