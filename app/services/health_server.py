"""Tiny HTTP health server for Render Web Service single-process deployments.

Telegram polling bots normally do not expose an HTTP port. Render Web Services
expect a process to bind to $PORT, so this optional server answers health checks
while the Telegram bot and in-process dubbing queue continue running.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from app.config import settings
from app.services.logger_service import logger


class HealthHandler(BaseHTTPRequestHandler):
    server_version = "AIDubbingHealth/1.0"

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path not in {"/", "/health", "/healthz"}:
            self.send_response(404)
            self.end_headers()
            return

        body = json.dumps({"ok": True, "service": "ai-dubbing-bot"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A003 - inherited API name
        logger.debug("Health server: " + format, *args)


def start_health_server() -> Optional[ThreadingHTTPServer]:
    if not settings.enable_health_server:
        return None

    server = ThreadingHTTPServer((settings.health_server_host, settings.health_server_port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, name="health-server", daemon=True)
    thread.start()
    logger.info("Health server started on %s:%s", settings.health_server_host, settings.health_server_port)
    return server


def stop_health_server(server: Optional[ThreadingHTTPServer]) -> None:
    if not server:
        return
    try:
        server.shutdown()
        server.server_close()
        logger.info("Health server stopped")
    except Exception as exc:
        logger.warning("Could not stop health server: %s", exc)
