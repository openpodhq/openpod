"""Local player bridge (AC-2) — let an agent operate the browser player.

Topology: the agent's MCP server (``openpod-mcp``) hosts this bridge, a
loopback HTTP server. The browser tab connects OUT to it — Server-Sent Events
for command delivery, POST for results. When the agent calls a ``player_*`` MCP
tool, the bridge delivers the command envelope to the connected tab, which runs
it through the *same* action registry the UI uses and posts the result back.

Durable state commands (subscribe, queue, settings, …) issued while **no tab is
connected** are queued in an on-disk outbox and flushed, in order, the next time
a tab connects — "prepare my player while I'm away", G2-clean (nothing leaves
the machine). Session commands (play/seek/…) and reads need a live tab and fail
fast with ``player_not_connected`` when none is attached.

Security: loopback only (127.0.0.1). A 6-digit pairing code — shown by the CLI,
entered once in the player Settings — authorizes the tab, so a random local page
can't drive the player.

Stdlib only (house rule): ``http.server`` + ``json`` + ``threading``. No
``websockets`` dependency; SSE over plain HTTP is enough for a loopback push.
"""

from __future__ import annotations

import json
import queue
import secrets
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from .config import Workspace

DEFAULT_PORT = 8788
_MANIFEST_PATH = Path(__file__).with_name("player_manifest.json")
_HEARTBEAT_SEC = 15.0
_SENTINEL = object()  # unblocks an SSE sink on shutdown


def _load_manifest() -> dict[str, dict]:
    """Map action name -> spec, from the checked-in copy of the player manifest
    (generated from app/src/agent/manifest.ts — keep them in step)."""
    data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    return {t["name"]: t for t in data.get("tools", [])}


MANIFEST = _load_manifest()
QUEUEABLE = {name for name, t in MANIFEST.items() if t.get("queueable")}


def _gen_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


class _Pending:
    __slots__ = ("event", "result")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Any = None


class PlayerBridge:
    """The loopback relay. Thread-safe; one instance per MCP-server process."""

    def __init__(
        self,
        workspace: Optional[Workspace] = None,
        *,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        code: Optional[str] = None,
    ) -> None:
        self.ws = workspace or Workspace()
        self.host = host
        self.port = port
        self.code = code or _gen_code()
        self._outbox_path = self.ws.dot / "bridge-outbox.json"
        self._lock = threading.RLock()
        self._sinks: set[queue.Queue] = set()  # one per connected tab
        self._pending: dict[str, _Pending] = {}
        self._outbox: list[dict] = self._load_outbox()
        self._stopping = threading.Event()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle --------------------------------------------------------- #

    def start(self) -> "PlayerBridge":
        handler = _make_handler(self)
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self.port = self._server.server_address[1]  # resolve an ephemeral :0
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stopping.set()
        with self._lock:
            for sink in list(self._sinks):
                sink.put(_SENTINEL)
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    def status(self) -> dict:
        with self._lock:
            return {
                "connected": len(self._sinks) > 0,
                "outbox": len(self._outbox),
                "port": self.port,
            }

    # -- outbox persistence ------------------------------------------------ #

    def _load_outbox(self) -> list[dict]:
        try:
            raw = json.loads(self._outbox_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, list) else []
        except (OSError, ValueError):
            return []

    def _save_outbox(self) -> None:
        self.ws.dot.mkdir(parents=True, exist_ok=True)
        self._outbox_path.write_text(json.dumps(self._outbox), encoding="utf-8")

    # -- command entry point (called by the MCP tools) --------------------- #

    def call(self, action: dict, *, timeout: float = 10.0) -> dict:
        """Run one PlayerAction. Delivers live to a connected tab and awaits the
        result; if none is connected, queues durable state acts (returns
        ``queued``) or fails fast for session acts/reads."""
        name = action.get("type", "")
        envelope = {"id": uuid.uuid4().hex, "source": "bridge", "action": action}

        with self._lock:
            connected = len(self._sinks) > 0

        if not connected:
            if name in QUEUEABLE:
                self._enqueue(envelope)
                return {
                    "ok": True,
                    "queued": True,
                    "id": envelope["id"],
                    "note": "No player tab connected — queued; runs when the player next connects.",
                }
            return {
                "ok": False,
                "error": {
                    "code": "player_not_connected",
                    "message": "No player tab is connected to the bridge.",
                    "fix": "Open the OpenPod player and connect the bridge in Settings.",
                },
            }

        pending = _Pending()
        with self._lock:
            self._pending[envelope["id"]] = pending
        self._deliver(envelope)
        if pending.event.wait(timeout):
            return pending.result
        with self._lock:
            self._pending.pop(envelope["id"], None)
        return {
            "ok": False,
            "error": {"code": "timeout", "message": "The player did not respond in time."},
        }

    # -- internal delivery / results --------------------------------------- #

    def _deliver(self, envelope: dict) -> None:
        with self._lock:
            sinks = list(self._sinks)
        for sink in sinks:
            sink.put(envelope)

    def _enqueue(self, envelope: dict) -> None:
        with self._lock:
            if any(e["id"] == envelope["id"] for e in self._outbox):
                return  # idempotent
            self._outbox.append(envelope)
            self._save_outbox()

    def _register_sink(self) -> queue.Queue:
        sink: queue.Queue = queue.Queue()
        with self._lock:
            self._sinks.add(sink)
            pending_outbox = list(self._outbox)
        # Flush the outbox to the newly-connected tab, in order.
        for env in pending_outbox:
            sink.put(env)
        return sink

    def _unregister_sink(self, sink: queue.Queue) -> None:
        with self._lock:
            self._sinks.discard(sink)

    def _on_result(self, cmd_id: str, result: dict) -> None:
        with self._lock:
            pending = self._pending.pop(cmd_id, None)
            in_outbox = any(e["id"] == cmd_id for e in self._outbox)
            if in_outbox:
                self._outbox = [e for e in self._outbox if e["id"] != cmd_id]
                self._save_outbox()
        if pending is not None:
            pending.result = result
            pending.event.set()

    def _valid_token(self, token: Optional[str]) -> bool:
        return bool(token) and secrets.compare_digest(token, self.code)


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #


def _make_handler(bridge: PlayerBridge):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:  # keep the CLI quiet
            pass

        def _cors(self) -> None:
            origin = self.headers.get("Origin", "*")
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "content-type")

        def _json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:  # CORS preflight for the POST
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/bridge/health":
                st = bridge.status()
                self._json(200, {"ok": True, "connected": st["connected"], "outbox": st["outbox"]})
                return
            if parsed.path == "/bridge/events":
                self._sse(parse_qs(parsed.query))
                return
            self._json(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/bridge/results":
                self._json(404, {"ok": False, "error": "not_found"})
                return
            token = parse_qs(parsed.query).get("token", [None])[0]
            if not bridge._valid_token(token):
                self._json(403, {"ok": False, "error": "forbidden"})
                return
            length = int(self.headers.get("Content-Length", 0) or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._json(400, {"ok": False, "error": "bad_json"})
                return
            cmd_id = body.get("id")
            if not cmd_id:
                self._json(400, {"ok": False, "error": "missing_id"})
                return
            bridge._on_result(cmd_id, body.get("result", {}))
            self._json(200, {"ok": True})

        def _sse(self, qs: dict) -> None:
            token = qs.get("token", [None])[0]
            if not bridge._valid_token(token):
                self._json(403, {"ok": False, "error": "forbidden"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self._cors()
            self.end_headers()

            sink = bridge._register_sink()
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while not bridge._stopping.is_set():
                    try:
                        item = sink.get(timeout=_HEARTBEAT_SEC)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")  # keep-alive + dead-peer probe
                        self.wfile.flush()
                        continue
                    if item is _SENTINEL:
                        break
                    payload = json.dumps(item).encode("utf-8")
                    self.wfile.write(b"data: " + payload + b"\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # tab went away
            finally:
                bridge._unregister_sink(sink)

    return Handler
