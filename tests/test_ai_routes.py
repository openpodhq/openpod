"""The /ai/* routes — auth, turn lifecycle, and streaming.

Like ``test_bridge.py`` these drive the real loopback HTTP server rather than
mocking it, and stay fully offline: a stub adapter is registered that spawns a
trivial Python process instead of a real agent CLI.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

import openpod.providers as providers
from openpod.bridge import PlayerBridge
from openpod.config import Workspace
from openpod.providers import Adapter, Detection


class StubAdapter(Adapter):
    """Emits two text events and exits — a real subprocess, canned output."""

    id = "stub"
    label = "Stub"

    def detect(self) -> Detection:
        return Detection(id=self.id, label=self.label, available=True, path="stub")

    def build_argv(self, req):
        emit = (
            'import json,sys\n'
            'for o in ['
            '{"type":"system","subtype":"init","session_id":"sess-9","tools":[]},'
            '{"type":"text","text":"Hel"},'
            '{"type":"text","text":"lo"}]:\n'
            '    sys.stdout.write(json.dumps(o)+"\\n"); sys.stdout.flush()\n'
        )
        return [sys.executable, "-c", emit]

    def observed_tools(self, obj):
        if obj.get("type") == "system" and obj.get("subtype") == "init":
            return list(obj.get("tools") or [])
        return None

    def translate(self, obj):
        if obj.get("type") == "system" and obj.get("session_id"):
            yield {"type": "session", "sessionId": obj["session_id"]}
        elif obj.get("type") == "text":
            yield {"type": "text", "text": obj["text"]}


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setitem(providers.ADAPTERS, "stub", StubAdapter())
    ws = Workspace(tmp_path)
    ws.ensure()
    b = PlayerBridge(ws, port=0).start()
    try:
        yield b
    finally:
        b.stop()


def _post(bridge, path: str, body: dict, *, token: str | None = None, host: str | None = None):
    token = bridge.code if token is None else token
    req = urllib.request.Request(
        f"http://127.0.0.1:{bridge.port}{path}?token={token}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **({"Host": host} if host else {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def _get(bridge, path: str, *, token: str | None = None, host: str | None = None):
    token = bridge.code if token is None else token
    req = urllib.request.Request(
        f"http://127.0.0.1:{bridge.port}{path}{'&' if '?' in path else '?'}token={token}",
        headers={"Host": host} if host else {},
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def _drain_stream(bridge, run_id: str, timeout: float = 15.0) -> list[dict]:
    url = f"http://127.0.0.1:{bridge.port}/ai/stream?run={run_id}&token={bridge.code}"
    events: list[dict] = []
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            events.append(event)
            if event.get("type") == "done":
                break
    return events


# --------------------------------------------------------------------------- #
# Auth — the /ai routes are no softer than the rest of the bridge
# --------------------------------------------------------------------------- #


def test_providers_requires_the_pairing_code(bridge):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(bridge, "/ai/providers", token="000000")
    assert e.value.code == 403


def test_providers_rejects_a_spoofed_host(bridge):
    # DNS-rebinding: a public page resolving its own domain to 127.0.0.1.
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(bridge, "/ai/providers", host="evil.example.com")
    assert e.value.code == 403


def test_turn_rejects_a_spoofed_host(bridge):
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(bridge, "/ai/turn", {"provider": "stub", "prompt": "hi"},
              host="evil.example.com")
    assert e.value.code == 403


def test_stream_requires_the_pairing_code(bridge):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(bridge, "/ai/stream?run=whatever", token="000000")
    assert e.value.code == 403


def test_providers_lists_the_real_adapters(bridge):
    ids = {p["id"] for p in _get(bridge, "/ai/providers")["providers"]}
    assert {"claude_local", "codex_local"} <= ids


# --------------------------------------------------------------------------- #
# Turn lifecycle
# --------------------------------------------------------------------------- #


def test_an_unknown_provider_is_refused_with_a_fix(bridge):
    out = _post(bridge, "/ai/turn", {"provider": "nope", "prompt": "hi"})
    assert out["ok"] is False
    assert out["error"]["code"] == "unknown_provider"
    assert out["error"]["fix"]


def test_an_empty_prompt_is_refused(bridge):
    out = _post(bridge, "/ai/turn", {"provider": "stub", "prompt": "   "})
    assert out["ok"] is False and out["error"]["code"] == "empty_prompt"


def test_a_turn_streams_its_answer_and_terminates(bridge):
    started = _post(bridge, "/ai/turn", {"provider": "stub", "prompt": "hi"})
    assert started["ok"] and started["runId"]

    events = _drain_stream(bridge, started["runId"])
    assert [e for e in events if e["type"] == "session"][0]["sessionId"] == "sess-9"
    assert "".join(e["text"] for e in events if e["type"] == "text") == "Hello"
    assert events[-1] == {"type": "done", "ok": True}


def test_posting_a_turn_returns_before_the_model_finishes(bridge):
    # The tab must get its runId immediately so it can open the stream; a
    # blocking /ai/turn would stall the UI for the length of the answer.
    t0 = time.time()
    out = _post(bridge, "/ai/turn", {"provider": "stub", "prompt": "hi"})
    assert out["ok"] and (time.time() - t0) < 3.0


def test_streaming_an_unknown_run_is_a_clean_404(bridge):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(bridge, "/ai/stream?run=does-not-exist")
    assert e.value.code == 404


def test_cancelling_an_unknown_run_is_reported_not_crashed(bridge):
    out = _post(bridge, "/ai/cancel", {"runId": "nope"})
    assert out["ok"] is False and out["error"]["code"] == "unknown_run"


def test_a_run_can_be_cancelled(bridge):
    started = _post(bridge, "/ai/turn", {"provider": "stub", "prompt": "hi"})
    out = _post(bridge, "/ai/cancel", {"runId": started["runId"]})
    assert out == {"ok": True, "cancelled": True}


def test_finished_runs_do_not_accumulate(bridge):
    # A long-lived bridge must not keep one entry per question ever asked.
    first = _post(bridge, "/ai/turn", {"provider": "stub", "prompt": "hi"})
    _drain_stream(bridge, first["runId"])
    deadline = time.time() + 5
    while time.time() < deadline and bridge._ai_runs:
        _post(bridge, "/ai/turn", {"provider": "stub", "prompt": "again"})
        time.sleep(0.1)
    assert len(bridge._ai_runs) <= 1


# --------------------------------------------------------------------------- #
# The MCP config handed to the spawned agent
# --------------------------------------------------------------------------- #


def test_agent_mcp_config_points_at_openpod_and_is_not_world_readable(bridge):
    path = bridge.agent_mcp_config()
    assert json.loads(path.read_text())["mcpServers"]["openpod"]["command"] == "openpod-mcp"
    assert (path.stat().st_mode & 0o077) == 0
