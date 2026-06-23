"""Tiny HTTP health server for Render Web Service single-process deployments.

Telegram polling bots normally do not expose an HTTP port. Render Web Services
expect the process to bind to 0.0.0.0:$PORT. This tiny server binds early and
answers health checks while the Telegram polling bot and in-process worker run.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from app.config import settings
from app.services.logger_service import logger

_HEALTH_SERVER: Optional[ThreadingHTTPServer] = None
_HEALTH_LOCK = threading.Lock()


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class HealthHandler(BaseHTTPRequestHandler):
    server_version = "AIDubbingHealth/1.1"

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path not in {"/", "/health", "/healthz", "/ready"}:
            self.send_response(404)
            self.end_headers()
            return

        body = json.dumps(
            {
                "ok": True,
                "service": "ai-dubbing-bot",
                "mode": "telegram-polling-single-service",
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A003 - inherited API name
        logger.debug("Health server: " + format, *args)


def start_health_server(force: bool = False) -> Optional[ThreadingHTTPServer]:
    """Start a small HTTP server for Render Web Service port detection.

    The function is idempotent because app.main starts it early and post_init may
    call it again. Binding early prevents Render from showing "No open ports
    detected" while Telegram/Supabase/Redis startup checks are still running.
    """
    global _HEALTH_SERVER

    if not force and not settings.enable_health_server:
        logger.info("Health server disabled. Set ENABLE_HEALTH_SERVER=true for Render Web Service.")
        return None

    with _HEALTH_LOCK:
        if _HEALTH_SERVER is not None:
            return _HEALTH_SERVER

        try:
            server = _ReusableThreadingHTTPServer(
                (settings.health_server_host, settings.health_server_port),
                HealthHandler,
            )
        except OSError as exc:
            logger.error(
                "Could not bind health server on %s:%s. For Render Web Service, bind 0.0.0.0:$PORT. Error: %s",
                settings.health_server_host,
                settings.health_server_port,
                exc,
            )
            raise

        thread = threading.Thread(target=server.serve_forever, name="health-server", daemon=True)
        thread.start()
        _HEALTH_SERVER = server
        logger.info("Health server started on %s:%s", settings.health_server_host, settings.health_server_port)
        return _HEALTH_SERVER


def stop_health_server(server: Optional[ThreadingHTTPServer] = None) -> None:
    global _HEALTH_SERVER

    target = server or _HEALTH_SERVER
    if not target:
        return

    with _HEALTH_LOCK:
        try:
            target.shutdown()
            target.server_close()
            logger.info("Health server stopped")
        except Exception as exc:  # noqa: BLE001 - shutdown must not crash process
            logger.warning("Could not stop health server: %s", exc)
        finally:
            if target is _HEALTH_SERVER:
                _HEALTH_SERVER = None
