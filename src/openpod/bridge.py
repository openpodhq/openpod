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
import os
import queue
import secrets
import threading
import time
import urllib.error
import urllib.request
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
_MAX_AUTH_FAILS = 20  # lock the bridge after this many wrong tokens (anti-brute-force)
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _atomic_write(path: Path, text: str, *, mode: int = 0o600) -> None:
    """Write a file all-or-nothing at a fixed mode: create a sibling temp at
    `mode`, write, then os.replace onto the target. A crash mid-write leaves the
    prior file intact; the secret is never briefly world-readable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _load_manifest() -> dict[str, dict]:
    """Map action name -> spec, from the checked-in copy of the player manifest.

    A byte-for-byte copy of the player's generated docs/agent-manifest.json
    (itself rendered from app/src/agent/manifest.ts). Each entry becomes one
    ``player_*`` MCP tool, so a stale copy costs agents whole tools silently —
    tests/test_player_manifest_parity.py is what keeps the two in step.
    """
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


class _AiRun:
    """One in-flight "Ask your AI" turn.

    The turn runs on its own thread pumping normalized provider events into a
    queue; the tab drains that queue over SSE. Decoupling them means a tab that
    reloads mid-answer doesn't kill the run, and a slow reader doesn't stall the
    provider.
    """

    __slots__ = ("id", "events", "cancel", "thread", "started")

    def __init__(self, run_id: str) -> None:
        self.id = run_id
        self.events: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.started = time.time()


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
        self._sinks: set[queue.Queue] = set()  # at most one active tab (single controller)
        self._pending: dict[str, _Pending] = {}
        self._auth_fails = 0  # cumulative wrong tokens; lock at _MAX_AUTH_FAILS
        self._outbox: list[dict] = self._load_outbox()
        self._ai_runs: dict[str, _AiRun] = {}
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
        self._write_discovery()
        return self

    def stop(self) -> None:
        self._stopping.set()
        with self._lock:
            for sink in list(self._sinks):
                sink.put(_SENTINEL)
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        self._clear_discovery()

    @property
    def discovery_path(self) -> Path:
        return self.ws.dot / "bridge.json"

    def _write_discovery(self) -> None:
        """Publish {port, code} so same-machine MCP tools can find + auth to the
        bridge. Contains the pairing secret → written 0600 from creation."""
        _atomic_write(
            self.discovery_path,
            json.dumps({"port": self.port, "code": self.code}),
            mode=0o600,
        )

    def _clear_discovery(self) -> None:
        try:
            self.discovery_path.unlink()
        except OSError:
            pass

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
        # Atomic + 0600: a crash mid-write never corrupts the durable queue, and
        # the queued command envelopes aren't world-readable.
        _atomic_write(self._outbox_path, json.dumps(self._outbox), mode=0o600)

    # -- command entry point (called by the MCP tools) --------------------- #

    def call(self, action: dict, *, timeout: float = 10.0) -> dict:
        """Run one PlayerAction. Delivers live to a connected tab and awaits the
        result; if none is connected, queues durable state acts (returns
        ``queued``) or fails fast for session acts/reads."""
        name = action.get("type", "")
        envelope = {"id": uuid.uuid4().hex, "source": "bridge", "action": action}
        not_connected = {
            "ok": False,
            "error": {
                "code": "player_not_connected",
                "message": "No player tab is connected to the bridge.",
                "fix": "Open the OpenPod player and connect the bridge in Settings.",
            },
        }

        # Decide connected-vs-queue-vs-fail AND register the pending / enqueue
        # under ONE lock, so a tab connecting at this instant can't strand the
        # command (its _register_sink either sees the queued item or we see its
        # sink). RLock lets _enqueue re-acquire.
        pending: Optional[_Pending] = None
        with self._lock:
            if self._sinks:
                pending = _Pending()
                self._pending[envelope["id"]] = pending
            elif name in QUEUEABLE:
                self._enqueue(envelope)
                return {
                    "ok": True,
                    "queued": True,
                    "id": envelope["id"],
                    "note": "No player tab connected — queued; runs when the player next connects.",
                }
            else:
                return not_connected

        # Deliver outside the lock (I/O). If the tab vanished in between so no
        # sink received it, fail fast rather than block the full timeout.
        if self._deliver(envelope) == 0:
            with self._lock:
                self._pending.pop(envelope["id"], None)
            return not_connected
        if pending.event.wait(timeout):
            return pending.result
        with self._lock:
            self._pending.pop(envelope["id"], None)
        return {
            "ok": False,
            "error": {"code": "timeout", "message": "The player did not respond in time."},
        }

    # -- internal delivery / results --------------------------------------- #

    def _deliver(self, envelope: dict) -> int:
        """Deliver to the active sink(s). Returns how many received it."""
        with self._lock:
            sinks = list(self._sinks)
        for sink in sinks:
            sink.put(envelope)
        return len(sinks)

    def _enqueue(self, envelope: dict) -> None:
        with self._lock:
            if any(e["id"] == envelope["id"] for e in self._outbox):
                return  # idempotent
            self._outbox.append(envelope)
            self._save_outbox()

    def _register_sink(self) -> queue.Queue:
        """Register the controlling tab. Single active sink: any existing sink is
        EVICTED (newest tab wins). This keeps reconnects robust — a stale prior
        connection is replaced rather than blocking the new one — and guarantees
        a command is only ever delivered to ONE tab, so it can't execute twice.
        (Two tabs both driving one bridge will ping-pong; rare, and neither
        double-executes.)"""
        sink: queue.Queue = queue.Queue()
        with self._lock:
            for old in list(self._sinks):
                old.put(_SENTINEL)
                self._sinks.discard(old)
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

    # -- "Ask your AI" turns ------------------------------------------------ #

    def agent_mcp_config(self) -> Path:
        """Write (and return) the MCP config handed to the spawned agent.

        This is the whole trick behind the feature: ``openpod-mcp`` already
        re-exports every player action as a ``player_*`` tool that calls back
        through this bridge to the tab. Point the agent at it and the chat can
        drive the player without a line of new agent-loop code.
        """
        path = self.ws.dot / "agent-mcp.json"
        _atomic_write(
            path,
            json.dumps({"mcpServers": {"openpod": {"command": "openpod-mcp"}}}),
            mode=0o600,
        )
        return path

    def start_ai_turn(self, body: dict) -> dict:
        """Spawn a turn and return its run id. Never blocks on the model."""
        from .providers import TurnRequest, get_adapter, run_turn

        adapter = get_adapter(str(body.get("provider") or "claude_local"))
        if adapter is None:
            return {"ok": False, "error": {
                "code": "unknown_provider",
                "message": f"No AI app called {body.get('provider')!r}.",
                "fix": "Pick one of the providers from /ai/providers.",
            }}

        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            return {"ok": False, "error": {"code": "empty_prompt",
                                           "message": "Nothing to ask."}}

        req = TurnRequest(
            prompt=prompt,
            system=body.get("system") or None,
            model=body.get("model") or None,
            effort=body.get("effort") or None,
            session_id=body.get("sessionId") or None,
            mcp_config=self.agent_mcp_config(),
            cwd=self.ws.root,
        )
        run = _AiRun(uuid.uuid4().hex)
        with self._lock:
            self._reap_ai_runs()
            self._ai_runs[run.id] = run

        def _pump() -> None:
            try:
                for event in run_turn(adapter, req, cancel=run.cancel):
                    run.events.put(event)
            except Exception as e:  # never let a provider bug wedge the thread
                run.events.put({"type": "error", "code": "internal",
                                "message": str(e), "fix": None})
                run.events.put({"type": "done", "ok": False})
            finally:
                run.events.put(_SENTINEL)

        run.thread = threading.Thread(target=_pump, daemon=True)
        run.thread.start()
        return {"ok": True, "runId": run.id}

    def _reap_ai_runs(self) -> None:
        """Drop finished runs. Called under the lock; keeps a long-lived bridge
        from accumulating one entry per question ever asked."""
        for rid, run in list(self._ai_runs.items()):
            if run.thread is not None and not run.thread.is_alive():
                del self._ai_runs[rid]

    def cancel_ai_turn(self, run_id: str) -> dict:
        with self._lock:
            run = self._ai_runs.get(run_id)
        if run is None:
            return {"ok": False, "error": {"code": "unknown_run",
                                           "message": "That turn is not running."}}
        run.cancel.set()
        return {"ok": True, "cancelled": True}

    def ai_events(self, run_id: str) -> Optional[queue.Queue]:
        with self._lock:
            run = self._ai_runs.get(run_id)
        return run.events if run is not None else None

    def _valid_token(self, token: Optional[str]) -> bool:
        """Constant-time token check with a cumulative lockout. After
        _MAX_AUTH_FAILS wrong tokens the bridge refuses all auth until restart —
        a 6-digit code is otherwise brute-forceable over loopback. A correct
        token resets the counter, so a legit tab's reconnects never lock it out."""
        with self._lock:
            if self._auth_fails >= _MAX_AUTH_FAILS:
                return False
            ok = bool(token) and secrets.compare_digest(token, self.code)
            if ok:
                self._auth_fails = 0
            else:
                self._auth_fails += 1
            return ok

    @property
    def locked_out(self) -> bool:
        with self._lock:
            return self._auth_fails >= _MAX_AUTH_FAILS


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #


def _make_handler(bridge: PlayerBridge):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:  # keep the CLI quiet
            pass

        def _host_ok(self) -> bool:
            """Reject any Host that isn't loopback — blocks DNS-rebinding, where
            a public page resolves an attacker domain to 127.0.0.1 to bypass the
            same-origin/loopback boundary."""
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            return host == "" or host in {"127.0.0.1", "localhost", "::1"}

        def _cors(self) -> None:
            # Reflect Origin ONLY on authorized/liveness responses. Failure
            # responses carry no CORS, so a cross-origin page can't read them —
            # closing the token-validity oracle that made the code guessable.
            origin = self.headers.get("Origin")
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")

        def _json(self, code: int, payload: dict, *, cors: bool = False) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if cors:
                self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _drain(self) -> bytes:
            """Read the request body so an early return can't desync the next
            keep-alive request on this connection."""
            length = int(self.headers.get("Content-Length", 0) or 0)
            return self.rfile.read(length) if length > 0 else b""

        def do_OPTIONS(self) -> None:  # CORS preflight (carries no token)
            self.send_response(204)
            self._cors()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "content-type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            if not self._host_ok():
                self._json(403, {"ok": False, "error": "bad_host"})
                return
            parsed = urlparse(self.path)
            if parsed.path == "/bridge/health":
                # Bare liveness only — no connected/outbox state leaked. CORS so
                # the tab can probe "bridge up?" to tell a wrong code from a
                # dead port.
                self._json(200, {"ok": True}, cors=True)
                return
            if parsed.path == "/bridge/events":
                self._sse(parse_qs(parsed.query))
                return
            qs = parse_qs(parsed.query)
            if parsed.path == "/ai/providers":
                if not bridge._valid_token(qs.get("token", [None])[0]):
                    self._json(403, {"ok": False, "error": "forbidden"})  # no CORS
                    return
                from .providers import detect_all

                self._json(200, {"ok": True, "providers": detect_all()}, cors=True)
                return
            if parsed.path == "/ai/stream":
                self._ai_stream(qs)
                return
            self._json(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:
            if not self._host_ok():
                self._drain()
                self._json(403, {"ok": False, "error": "bad_host"})
                return
            parsed = urlparse(self.path)
            token = parse_qs(parsed.query).get("token", [None])[0]
            raw = self._drain()  # always consume the body (keep-alive safety)
            if parsed.path not in ("/bridge/results", "/bridge/call",
                                   "/ai/turn", "/ai/cancel"):
                self._json(404, {"ok": False, "error": "not_found"})
                return
            if not bridge._valid_token(token):
                self._json(403, {"ok": False, "error": "forbidden"})  # no CORS
                return
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                self._json(400, {"ok": False, "error": "bad_json"}, cors=True)
                return

            if parsed.path == "/ai/turn":
                self._json(200, bridge.start_ai_turn(body), cors=True)
                return

            if parsed.path == "/ai/cancel":
                run_id = str(body.get("runId") or "")
                self._json(200, bridge.cancel_ai_turn(run_id), cors=True)
                return

            if parsed.path == "/bridge/call":
                # The agent's MCP tool calling in: relay to the tab, block for
                # the result (or queue/fail), return it as the HTTP response.
                action = body.get("action")
                if not isinstance(action, dict) or "type" not in action:
                    self._json(400, {"ok": False, "error": "bad_action"}, cors=True)
                    return
                self._json(200, bridge.call(action), cors=True)
                return

            # /bridge/results — the tab returning a command outcome.
            cmd_id = body.get("id")
            if not cmd_id:
                self._json(400, {"ok": False, "error": "missing_id"}, cors=True)
                return
            bridge._on_result(cmd_id, body.get("result", {}))
            self._json(200, {"ok": True}, cors=True)

        def _sse(self, qs: dict) -> None:
            token = qs.get("token", [None])[0]
            if not bridge._valid_token(token):
                self._json(403, {"ok": False, "error": "forbidden"})  # no CORS
                return
            sink = bridge._register_sink()  # evicts any prior tab (newest wins)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self._cors()
            self.end_headers()

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

        def _ai_stream(self, qs: dict) -> None:
            """Stream one turn's events to the tab.

            Separate from /bridge/events on purpose: that channel carries
            commands INTO the tab and must stay open for the tab's whole life,
            while this one is per-question and ends with the turn. The agent's
            tool calls still travel the old channel — this is only the answer
            coming back.
            """
            if not bridge._valid_token(qs.get("token", [None])[0]):
                self._json(403, {"ok": False, "error": "forbidden"})  # no CORS
                return
            events = bridge.ai_events(qs.get("run", [""])[0])
            if events is None:
                self._json(404, {"ok": False, "error": "unknown_run"}, cors=True)
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self._cors()
            self.end_headers()
            try:
                while not bridge._stopping.is_set():
                    try:
                        item = events.get(timeout=_HEARTBEAT_SEC)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    if item is _SENTINEL:
                        break
                    self.wfile.write(b"data: " + json.dumps(item).encode("utf-8") + b"\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # the tab closed the answer stream; the run finishes anyway

    return Handler


# --------------------------------------------------------------------------- #
# Client side — used by the MCP tools to reach a running bridge process
# --------------------------------------------------------------------------- #


def read_discovery(ws: Workspace) -> Optional[dict]:
    """Read {port, code} written by a running bridge, or None."""
    try:
        return json.loads((ws.dot / "bridge.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def bridge_call(ws: Workspace, action: dict, *, timeout: float = 12.0) -> dict:
    """Send one PlayerAction to the running bridge and return its result. Used by
    the ``player_*`` MCP tools — a thin loopback HTTP client to the hub."""
    disc = read_discovery(ws)
    if not disc:
        return {
            "ok": False,
            "error": {
                "code": "bridge_not_running",
                "message": "The player bridge isn't running.",
                "fix": "Start it in a terminal: openpod player-bridge",
            },
        }
    url = f"http://127.0.0.1:{disc['port']}/bridge/call?token={disc['code']}"
    data = json.dumps({"action": action}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.URLError as e:
        return {
            "ok": False,
            "error": {
                "code": "bridge_unreachable",
                "message": f"Could not reach the bridge: {e}",
                "fix": "Is `openpod player-bridge` still running?",
            },
        }
