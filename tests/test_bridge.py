"""Integration tests for the local player bridge (AC-2).

A ``FakeTab`` plays the role of the browser: it opens the SSE stream, reads
command envelopes, and POSTs canned results back — exercising the real HTTP
server over loopback (no mocks; the bridge IS a server).
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from openpod.bridge import PlayerBridge, bridge_call
from openpod.config import Workspace


def _wait(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


class FakeTab:
    """Simulates the player tab: SSE in, results out."""

    def __init__(self, port: int, code: str, responder):
        self.base = f"http://127.0.0.1:{port}"
        self.code = code
        self.responder = responder
        self.received: list[dict] = []
        self._stop = threading.Event()
        self._resp = None
        self._thread = None

    def connect(self) -> "FakeTab":
        self._resp = urllib.request.urlopen(
            f"{self.base}/bridge/events?token={self.code}", timeout=5
        )
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                line = self._resp.readline()
                if not line:
                    break
                if line.startswith(b"data: "):
                    env = json.loads(line[6:].strip())
                    self.received.append(env)
                    self._post_result(env["id"], self.responder(env))
        except Exception:
            pass

    def _post_result(self, cmd_id: str, result: dict) -> None:
        data = json.dumps({"id": cmd_id, "result": result}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base}/bridge/results?token={self.code}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5).read()

    def close(self) -> None:
        self._stop.set()
        try:
            if self._resp:
                self._resp.close()
        except Exception:
            pass


def _ok(env: dict, data=None) -> dict:
    return {"id": env["id"], "ok": True, "action": env["action"]["type"], "data": data or {}}


def test_live_delivery_returns_ground_truth(tmp_path):
    bridge = PlayerBridge(Workspace(tmp_path), port=0, code="123456").start()
    try:
        tab = FakeTab(bridge.port, "123456", lambda e: _ok(e, {"queue": []})).connect()
        assert _wait(lambda: bridge.status()["connected"])
        res = bridge.call({"type": "get_queue"}, timeout=5)
        assert res["ok"] is True
        assert res["data"] == {"queue": []}
        assert tab.received[0]["action"]["type"] == "get_queue"
        assert tab.received[0]["source"] == "bridge"
        tab.close()
    finally:
        bridge.stop()


def test_no_tab_queues_state_and_fails_session(tmp_path):
    bridge = PlayerBridge(Workspace(tmp_path), port=0).start()
    try:
        queued = bridge.call({"type": "subscribe", "url": "https://x"})
        assert queued["queued"] is True
        assert bridge.status()["outbox"] == 1

        session = bridge.call({"type": "play", "episode": "e"})
        assert session["ok"] is False
        assert session["error"]["code"] == "player_not_connected"

        read = bridge.call({"type": "get_queue"})
        assert read["error"]["code"] == "player_not_connected"  # reads need a tab too
    finally:
        bridge.stop()


def test_outbox_flushes_in_order_on_connect(tmp_path):
    code = "654321"
    bridge = PlayerBridge(Workspace(tmp_path), port=0, code=code).start()
    try:
        bridge.call({"type": "subscribe", "url": "https://a"})
        bridge.call({"type": "queue_add", "episode": "e1"})
        assert bridge.status()["outbox"] == 2

        got: list[dict] = []

        def responder(env):
            got.append(env)
            return _ok(env)

        tab = FakeTab(bridge.port, code, responder).connect()
        assert _wait(lambda: bridge.status()["outbox"] == 0)
        types = [e["action"]["type"] for e in got]
        assert types == ["subscribe", "queue_add"]  # in order
        tab.close()
    finally:
        bridge.stop()


def test_call_times_out_when_tab_never_answers(tmp_path):
    bridge = PlayerBridge(Workspace(tmp_path), port=0, code="222222").start()
    try:
        # A connected tab that reads commands but never posts a result.
        silent = FakeTab(bridge.port, "222222", lambda e: _ok(e))
        silent._post_result = lambda *a, **k: None  # type: ignore[assignment]
        silent.connect()
        assert _wait(lambda: bridge.status()["connected"])
        res = bridge.call({"type": "get_queue"}, timeout=0.4)
        assert res["ok"] is False
        assert res["error"]["code"] == "timeout"
        silent.close()
    finally:
        bridge.stop()


def test_bad_token_is_forbidden(tmp_path):
    bridge = PlayerBridge(Workspace(tmp_path), port=0, code="111111").start()
    try:
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(
                f"http://127.0.0.1:{bridge.port}/bridge/events?token=wrong", timeout=3
            )
        assert ei.value.code == 403
    finally:
        bridge.stop()


def test_enqueue_is_idempotent(tmp_path):
    bridge = PlayerBridge(Workspace(tmp_path), port=0)
    env = {"id": "fixed", "source": "bridge", "action": {"type": "subscribe", "url": "x"}}
    bridge._enqueue(env)
    bridge._enqueue(env)
    assert len(bridge._outbox) == 1


def test_outbox_persists_across_restart(tmp_path):
    ws = Workspace(tmp_path)
    b1 = PlayerBridge(ws, port=0)
    b1.call({"type": "subscribe", "url": "https://x"})  # no server -> queued
    b2 = PlayerBridge(ws, port=0)
    assert len(b2._outbox) == 1
    assert b2._outbox[0]["action"]["type"] == "subscribe"


def test_bridge_call_client_roundtrips_through_the_hub(tmp_path):
    ws = Workspace(tmp_path)
    bridge = PlayerBridge(ws, port=0, code="777777").start()
    try:
        tab = FakeTab(bridge.port, "777777", lambda e: _ok(e, {"queue": ["x"]})).connect()
        assert _wait(lambda: bridge.status()["connected"])
        # The MCP-tool path: a same-machine HTTP client using the discovery file.
        res = bridge_call(ws, {"type": "get_queue"}, timeout=5)
        assert res["ok"] is True and res["data"] == {"queue": ["x"]}
        tab.close()
    finally:
        bridge.stop()


def test_bridge_call_without_a_running_bridge(tmp_path):
    res = bridge_call(Workspace(tmp_path), {"type": "get_queue"})
    assert res["error"]["code"] == "bridge_not_running"


def test_health_is_bare_liveness_only(tmp_path):
    bridge = PlayerBridge(Workspace(tmp_path), port=0, code="333333").start()
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{bridge.port}/bridge/health", timeout=3)
        body = json.loads(resp.read())
        assert body == {"ok": True}  # no code, no connected/outbox state leaked
    finally:
        bridge.stop()


def _post(port, path, token, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}?token={token}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=3)


def test_auth_locks_out_after_repeated_wrong_tokens(tmp_path):
    bridge = PlayerBridge(Workspace(tmp_path), port=0, code="444444").start()
    try:
        for _ in range(20):
            with pytest.raises(urllib.error.HTTPError) as ei:
                _post(bridge.port, "/bridge/results", "000000", {"id": "x", "result": {}})
            assert ei.value.code == 403
        # Locked out: even the CORRECT code is now refused until restart.
        assert bridge.locked_out is True
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(
                f"http://127.0.0.1:{bridge.port}/bridge/events?token=444444", timeout=3
            )
        assert ei.value.code == 403
    finally:
        bridge.stop()


def test_newest_tab_becomes_the_sole_controller(tmp_path):
    code = "555555"
    bridge = PlayerBridge(Workspace(tmp_path), port=0, code=code).start()
    try:
        tab1 = FakeTab(bridge.port, code, lambda e: _ok(e)).connect()
        assert _wait(lambda: len(bridge._sinks) == 1)
        tab2 = FakeTab(bridge.port, code, lambda e: _ok(e, {"who": 2})).connect()
        # tab2 evicts tab1: still exactly one active sink.
        assert _wait(lambda: len(bridge._sinks) == 1)
        before = len(tab1.received)
        res = bridge.call({"type": "get_queue"}, timeout=5)
        assert res["data"] == {"who": 2}  # answered by tab2
        assert len(tab1.received) == before  # tab1 got nothing new
        tab1.close()
        tab2.close()
    finally:
        bridge.stop()


def test_non_loopback_host_is_rejected_dns_rebinding(tmp_path):
    bridge = PlayerBridge(Workspace(tmp_path), port=0, code="666666").start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{bridge.port}/bridge/health",
            headers={"Host": "evil.example.com"},
        )
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req, timeout=3)
        assert ei.value.code == 403
    finally:
        bridge.stop()


def test_discovery_file_is_0600(tmp_path):
    import os
    import stat

    if os.name != "posix":
        pytest.skip("permission bits are POSIX-only")
    bridge = PlayerBridge(Workspace(tmp_path), port=0, code="777000").start()
    try:
        mode = stat.S_IMODE(bridge.discovery_path.stat().st_mode)
        assert mode == 0o600
    finally:
        bridge.stop()
