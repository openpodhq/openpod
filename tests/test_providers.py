"""Provider adapters — argv hardening, event translation, and the safety gate.

These run fully offline. Instead of spawning a real agent CLI (network, an
account, and the user's quota), a tiny Python script plays the CLI and emits
canned JSONL on stdout — a real subprocess over a real pipe, so ``run_turn``'s
streaming, cancellation and exit handling are exercised for real.

The shapes asserted here were captured from actual ``claude -p
--output-format stream-json`` and ``codex exec --json`` runs.
"""

from __future__ import annotations

import json
import sys
import textwrap
import threading

import pytest

from openpod.providers import (
    BUILTIN_DENY,
    Adapter,
    TurnRequest,
    detect_all,
    get_adapter,
    run_turn,
)
from openpod.providers.cli_agent import ClaudeLocalAdapter, CodexLocalAdapter


# --------------------------------------------------------------------------- #
# A fake CLI: real process, canned stream.
# --------------------------------------------------------------------------- #


def _fake_cli(tmp_path, lines: list[dict], *, exit_code: int = 0, hang: bool = False):
    """Write a script that prints `lines` as JSONL, then return its path.

    `hang` keeps the process alive after the last line so a cancellation has
    something real to interrupt.
    """
    script = tmp_path / "fake_cli.py"
    payload = tmp_path / "fake_cli_lines.json"
    payload.write_text(json.dumps(lines), encoding="utf-8")
    body = textwrap.dedent(
        """
        import json, sys, time
        lines = json.loads(open(PAYLOAD, encoding="utf-8").read())
        for obj in lines:
            sys.stdout.write(json.dumps(obj) + "\\n")
            sys.stdout.flush()
        if HANG:
            time.sleep(30)
        sys.exit(EXIT_CODE)
        """
    ).strip()
    body = (
        body.replace("PAYLOAD", repr(str(payload)))
        .replace("HANG", repr(hang))
        .replace("EXIT_CODE", repr(exit_code))
    )
    script.write_text(body, encoding="utf-8")
    return script


class FakeAdapter(Adapter):
    """Runs the fake CLI, translating Claude-shaped events."""

    id = "fake"
    label = "Fake CLI"
    supports_effort = True

    def __init__(self, script):
        self.script = script

    def detect(self):
        from openpod.providers import Detection

        return Detection(id=self.id, label=self.label, available=True, path=str(self.script))

    def build_argv(self, req: TurnRequest) -> list[str]:
        return [sys.executable, str(self.script)]

    observed_tools = ClaudeLocalAdapter.observed_tools
    translate = ClaudeLocalAdapter.translate


def _init(tools, session="s-1"):
    return {"type": "system", "subtype": "init", "session_id": session, "tools": tools}


def _delta(text):
    return {
        "type": "stream_event",
        "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
    }


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #


def test_missing_executable_is_unavailable_with_a_reason():
    class Ghost(ClaudeLocalAdapter):
        executable = "definitely-not-installed-openpod"

    d = Ghost().detect()
    assert d.available is False
    assert "definitely-not-installed-openpod" in d.reason


def test_codex_is_declared_unavailable_because_its_shell_cannot_be_confined():
    # Installed-but-unsafe must surface a reason rather than vanish from the
    # picker — a greyed row with no explanation reads as a bug.
    d = CodexLocalAdapter().detect()
    assert d.available is False
    assert d.reason and "shell" in d.reason.lower()


def test_detect_all_is_json_safe():
    payload = detect_all()
    json.dumps(payload)  # must survive the /ai/providers response
    assert {p["id"] for p in payload} == {"claude_local", "codex_local"}


# --------------------------------------------------------------------------- #
# argv hardening — the sandbox is the feature
# --------------------------------------------------------------------------- #


def test_claude_argv_drops_user_settings_and_foreign_mcp_servers():
    argv = ClaudeLocalAdapter().build_argv(TurnRequest(prompt="hi"))
    # Without these the spawned agent inherits the user's hooks, plugins, and
    # every connected MCP server (Gmail, Drive, Calendar were observed).
    assert "--strict-mcp-config" in argv
    i = argv.index("--setting-sources")
    assert argv[i + 1] == ""


def test_claude_argv_denies_builtins_and_never_relies_on_an_allowlist():
    argv = ClaudeLocalAdapter().build_argv(TurnRequest(prompt="hi"))
    # --allowedTools is an ADDITIVE PERMISSION GRANT, not a restriction: a run
    # allowlisting one MCP tool still called Bash and read a canary off disk.
    assert "--allowedTools" not in argv
    assert "--disallowedTools" in argv
    for dangerous in ("Bash", "Read", "Write", "Edit", "WebFetch"):
        assert dangerous in argv, f"{dangerous} must be denied by name"


def test_claude_argv_carries_model_effort_and_system_prompt():
    argv = ClaudeLocalAdapter().build_argv(
        TurnRequest(prompt="hi", model="opus", effort="high", system="SYS")
    )
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "high"
    assert argv[argv.index("--system-prompt") + 1] == "SYS"


def test_claude_names_a_session_then_resumes_it():
    first = ClaudeLocalAdapter().build_argv(TurnRequest(prompt="hi"))
    assert "--session-id" in first and "--resume" not in first

    again = ClaudeLocalAdapter().build_argv(TurnRequest(prompt="hi", session_id="abc"))
    assert again[again.index("--resume") + 1] == "abc"
    assert "--session-id" not in again


# --------------------------------------------------------------------------- #
# Event translation
# --------------------------------------------------------------------------- #


def test_text_comes_from_deltas_only_so_replies_are_not_doubled():
    # With --include-partial-messages the CLI emits assistant text TWICE: as
    # deltas and again as the completed message. Rendering both duplicates
    # every reply.
    a = ClaudeLocalAdapter()
    out = list(a.translate(_delta("One"))) + list(a.translate(_delta(", two.")))
    completed = {"type": "assistant", "message": {"content": [{"type": "text", "text": "One, two."}]}}
    out += list(a.translate(completed))

    assert "".join(e["text"] for e in out if e["type"] == "text") == "One, two."


def test_tool_calls_come_from_the_completed_message_and_lose_the_mcp_prefix():
    a = ClaudeLocalAdapter()
    events = list(a.translate({
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use", "id": "t1",
            "name": "mcp__openpod__player_seek", "input": {"to": 1450},
        }]},
    }))
    assert events == [{"type": "tool_call", "id": "t1", "name": "player_seek", "input": {"to": 1450}}]


def test_tool_results_are_previewed_not_dumped_whole():
    a = ClaudeLocalAdapter()
    huge = [{"type": "text", "text": "x" * 5000}]
    (event,) = list(a.translate({
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": huge}]},
    }))
    # A transcript fetch would otherwise put 40k words into the chat log.
    assert event["ok"] is True
    assert len(event["preview"]) < 500 and event["preview"].endswith("…")


def test_codex_thread_started_is_the_session():
    (event,) = list(CodexLocalAdapter().translate(
        {"type": "thread.started", "thread_id": "019fc431"}
    ))
    assert event == {"type": "session", "sessionId": "019fc431"}


def test_codex_reports_whole_messages_rather_than_deltas():
    (event,) = list(CodexLocalAdapter().translate(
        {"type": "item.completed", "item": {"id": "i0", "type": "agent_message", "text": "OK"}}
    ))
    assert event == {"type": "text", "text": "OK"}


# --------------------------------------------------------------------------- #
# run_turn — streaming, the safety gate, cancellation
# --------------------------------------------------------------------------- #


def test_a_clean_run_streams_text_and_reports_done_ok(tmp_path):
    script = _fake_cli(tmp_path, [
        _init(["mcp__openpod__player_seek"]),
        _delta("Hel"), _delta("lo"),
    ])
    events = list(run_turn(FakeAdapter(script), TurnRequest(prompt="hi")))

    assert [e for e in events if e["type"] == "session"][0]["sessionId"] == "s-1"
    assert "".join(e["text"] for e in events if e["type"] == "text") == "Hello"
    assert events[-1] == {"type": "done", "ok": True}
    # Exactly one terminator, so the tab closes the stream on one condition.
    assert sum(1 for e in events if e["type"] == "done") == 1


def test_an_unsafe_tool_surface_aborts_before_any_model_output(tmp_path):
    # The denylist fails OPEN the day the CLI ships a tool we don't know about.
    # This gate is what actually holds: a podcast transcript must never reach
    # an agent holding a shell.
    script = _fake_cli(tmp_path, [
        _init(["mcp__openpod__player_seek", "Bash"]),
        _delta("I should not be rendered"),
    ])
    events = list(run_turn(FakeAdapter(script), TurnRequest(prompt="hi")))

    error = [e for e in events if e["type"] == "error"][0]
    assert error["code"] == "unsafe_tool_surface"
    assert "Bash" in error["message"]
    assert not [e for e in events if e["type"] == "text"]
    assert events[-1] == {"type": "done", "ok": False}


def test_an_empty_tool_surface_is_allowed(tmp_path):
    script = _fake_cli(tmp_path, [_init([]), _delta("fine")])
    events = list(run_turn(FakeAdapter(script), TurnRequest(prompt="hi")))
    assert events[-1] == {"type": "done", "ok": True}


def test_a_nonzero_exit_is_reported_as_an_error(tmp_path):
    script = _fake_cli(tmp_path, [_init([])], exit_code=3)
    events = list(run_turn(FakeAdapter(script), TurnRequest(prompt="hi")))
    assert [e for e in events if e["type"] == "error"][0]["code"] == "provider_failed"
    assert events[-1] == {"type": "done", "ok": False}


def test_cancelling_stops_the_run(tmp_path):
    script = _fake_cli(tmp_path, [_init([]), _delta("partial")], hang=True)
    cancel = threading.Event()
    events = []
    for event in run_turn(FakeAdapter(script), TurnRequest(prompt="hi"), cancel=cancel):
        events.append(event)
        if event["type"] == "text":
            cancel.set()  # user hit stop mid-stream
    assert events[-1] == {"type": "done", "ok": False}


def test_a_clean_run_is_not_mistaken_for_a_cancelled_one(tmp_path):
    # Regression: releasing the cancel-watcher by setting the caller's own
    # cancel event made every successful turn report done:false.
    script = _fake_cli(tmp_path, [_init([]), _delta("hi")])
    cancel = threading.Event()
    events = list(run_turn(FakeAdapter(script), TurnRequest(prompt="hi"), cancel=cancel))
    assert events[-1] == {"type": "done", "ok": True}
    assert not cancel.is_set()


def test_an_unavailable_provider_never_spawns_anything():
    events = list(run_turn(CodexLocalAdapter(), TurnRequest(prompt="hi")))
    assert events[0]["code"] == "provider_unavailable"
    assert events[-1] == {"type": "done", "ok": False}


def test_registry_exposes_the_known_providers():
    assert isinstance(get_adapter("claude_local"), ClaudeLocalAdapter)
    assert get_adapter("nope") is None
