"""Local, user-authorised bridge for a browser extension order reader.

The extension runs inside the user's already logged-in browser tab.  This
module deliberately receives only page text over loopback; it never imports
browser cookies, launches a hidden browser, or attempts to bypass challenges.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Signal

from modules.logger import logger


ORDER_PLATFORMS = {"c5", "eco", "igxe"}
TASK_ENDPOINT = "/api/v1/browser-bridge/task"
SNAPSHOT_ENDPOINT = "/api/v1/browser-bridge/order-snapshot"
TOKEN_HEADER = "X-CS2-Rental-Token"
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024


class BrowserBridgeServer(QObject):
    """Expose a tiny token-protected loopback API to the local extension."""

    snapshot_received = Signal(dict)
    server_error = Signal(str)

    def __init__(self, token: str, host: str = "127.0.0.1", port: int = 8765):
        super().__init__()
        self.token = token
        self.host = host
        self.port = port
        self._lock = threading.Lock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._server is not None and self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if self.is_running:
            return True
        try:
            server = ThreadingHTTPServer((self.host, self.port), self._make_handler())
            server.daemon_threads = True
        except OSError as exc:
            message = f"浏览器订单连接服务启动失败（{self.host}:{self.port}）：{exc}"
            logger.warning("[BrowserBridge] %s", message)
            self.server_error.emit(message)
            return False

        self._server = server
        self.port = int(server.server_address[1])
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="browser-order-bridge",
            daemon=True,
        )
        self._thread.start()
        logger.info("[BrowserBridge] 本地连接服务已启动: %s:%s", self.host, self.port)
        return True

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)

    def enqueue_task(self, platform: str) -> dict[str, Any] | None:
        """Request one page capture. A newer request replaces the old platform task."""
        if platform not in ORDER_PLATFORMS or not self.is_running:
            return None
        task = {
            "task_id": secrets.token_urlsafe(18),
            "platform": platform,
            "created_at": int(time.time()),
        }
        with self._lock:
            self._tasks[platform] = task
        return dict(task)

    def _next_task(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._tasks:
                return None
            return dict(min(self._tasks.values(), key=lambda item: item["created_at"]))

    def _accept_snapshot(self, payload: dict[str, Any]) -> tuple[bool, str]:
        task_id = str(payload.get("task_id", ""))
        platform = str(payload.get("platform", ""))
        if platform not in ORDER_PLATFORMS or not task_id:
            return False, "invalid task payload"

        with self._lock:
            task = self._tasks.get(platform)
            if not task or not hmac.compare_digest(task_id, task["task_id"]):
                return False, "no matching pending task"
            self._tasks.pop(platform, None)

        page_text = payload.get("page_text", "")
        if not isinstance(page_text, str):
            return False, "page_text must be a string"
        normalized = {
            "platform": platform,
            "task_id": task_id,
            "source_url": str(payload.get("source_url", ""))[:2048],
            "page_title": str(payload.get("page_title", ""))[:500],
            "page_text": page_text[:MAX_PAYLOAD_BYTES],
            "challenge_detected": bool(payload.get("challenge_detected", False)),
            "capture_error": str(payload.get("capture_error", ""))[:500],
            "captured_at": str(payload.get("captured_at", ""))[:100],
        }
        self.snapshot_received.emit(normalized)
        return True, "accepted"

    def _is_authorized(self, request: BaseHTTPRequestHandler) -> bool:
        supplied = request.headers.get(TOKEN_HEADER, "")
        return bool(self.token and hmac.compare_digest(supplied, self.token))

    def _make_handler(self):
        bridge = self

        class BridgeHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A003
                logger.debug("[BrowserBridge] " + format, *args)

            def _send(self, status: int, data: dict[str, Any]) -> None:
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self) -> bool:
                if bridge._is_authorized(self):
                    return True
                self._send(401, {"ok": False, "error": "unauthorized"})
                return False

            def do_GET(self):  # noqa: N802
                if urlparse(self.path).path != TASK_ENDPOINT:
                    self._send(404, {"ok": False, "error": "not found"})
                    return
                if not self._authorized():
                    return
                self._send(200, {"ok": True, "task": bridge._next_task()})

            def do_POST(self):  # noqa: N802
                if urlparse(self.path).path != SNAPSHOT_ENDPOINT:
                    self._send(404, {"ok": False, "error": "not found"})
                    return
                if not self._authorized():
                    return
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    content_length = 0
                if content_length <= 0 or content_length > MAX_PAYLOAD_BYTES:
                    self._send(413, {"ok": False, "error": "invalid payload size"})
                    return
                try:
                    payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._send(400, {"ok": False, "error": "invalid JSON"})
                    return
                if not isinstance(payload, dict):
                    self._send(400, {"ok": False, "error": "JSON object required"})
                    return
                accepted, message = bridge._accept_snapshot(payload)
                self._send(200 if accepted else 409, {"ok": accepted, "message": message})

        return BridgeHandler
