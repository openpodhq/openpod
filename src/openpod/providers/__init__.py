"""Provider adapters — run a turn of "Ask your AI" on the user's own AI app.

OpenPod itself never calls a model. The library pipeline is deliberately
LLM-free (see :mod:`openpod.briefing`, :mod:`openpod.transcript_md`) and stays
that way: this package does not add an OpenPod model client, an API key store,
or an account. It *shells out to the agent CLI the user already installed and
signed in to* — so the model call happens under the user's own subscription, in
their own tool, and OpenPod adds nothing to that trust relationship.

    tab ──POST /ai/turn──▶ bridge ──subprocess──▶ claude -p …
                                                    │ calls player_* / library
                                                    │ tools over the OpenPod
                                                    ▼ MCP server
    tab ◀──SSE /ai/stream── normalized events    (the existing bridge channel)

Every adapter normalizes its CLI's native JSONL into one event shape so the tab
renders one thing regardless of which AI app produced it:

    {"type": "session",     "sessionId": str}
    {"type": "text",        "text": str}                  # may be a delta or whole
    {"type": "tool_call",   "id": str, "name": str, "input": dict}
    {"type": "tool_result", "id": str, "ok": bool, "preview": str}
    {"type": "error",       "code": str, "message": str, "fix": str | None}
    {"type": "done",        "ok": bool}

**The sandbox is the load-bearing part of this module.** A podcast transcript is
untrusted third-party content, and we are feeding it to an agent running on the
user's machine. Three things were established by probing the real CLIs, each of
which a from-the-docs implementation gets wrong:

1. A bare spawn inherits *the user's whole environment*. A probe run came up
   holding the user's Gmail, Drive and Calendar MCP servers, 140+ subagents, and
   SessionStart hooks that injected an unrelated "start executing with real tool
   calls" directive into what is meant to be a podcast chat. ``--strict-mcp-config``
   plus an empty ``--setting-sources`` clears all of it.
2. ``--allowedTools`` does **not** restrict anything — it is an additive
   permission grant. A run allowlisting exactly one MCP tool still called
   ``Bash(ls -la)`` and ``Read``, and read back a canary file. An allowlist is
   not a boundary here.
3. ``--disallowedTools`` does restrict: enumerating the built-ins yields an empty
   tool list and the canary stays unread.

So we denylist the built-ins — and because a denylist silently fails *open* the
day the CLI ships a new tool, :func:`run_turn` also fails **closed**: it reads
the CLI's own init event and aborts the run if any tool outside the OpenPod MCP
surface is present. A new built-in then breaks the feature loudly instead of
quietly becoming reachable from a podcast feed.

Stdlib only (house rule): ``subprocess`` + ``json`` + ``threading``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

# Tools reachable from a chat turn: the OpenPod MCP surface and nothing else.
# Anything the CLI reports outside this prefix trips the fail-closed gate.
SAFE_TOOL_PREFIX = "mcp__openpod__"

# Every built-in tool observed in the CLI's init event, denied by name. This
# list is a floor, not a ceiling — the fail-closed gate in run_turn() is what
# actually holds when a future version adds something not listed here.
BUILTIN_DENY: tuple[str, ...] = (
    "Bash", "Edit", "Write", "Read", "Glob", "Grep", "NotebookEdit",
    "Task", "WebFetch", "WebSearch", "Artifact", "Skill", "Workflow",
    "ToolSearch", "CronCreate", "CronDelete", "CronList", "DesignSync",
    "EnterWorktree", "ExitWorktree", "Monitor", "PushNotification",
    "RemoteTrigger", "ReportFindings", "ScheduleWakeup", "SendMessage",
    "TaskCreate", "TaskGet", "TaskList", "TaskOutput", "TaskStop", "TaskUpdate",
)

# Effort ladder shared by the UI. An adapter that cannot honour effort declares
# supports_effort=False and the player disables the control rather than
# pretending the setting did something.
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")


@dataclass
class TurnRequest:
    """One user message plus everything needed to run it."""

    prompt: str
    system: Optional[str] = None
    model: Optional[str] = None
    effort: Optional[str] = None
    # The provider-side conversation id. None starts a new one; the adapter
    # reports the id it used via a "session" event so the tab can persist it and
    # pass it back next turn. Multi-turn memory is the CLI's job, not ours.
    session_id: Optional[str] = None
    mcp_config: Optional[Path] = None
    cwd: Optional[Path] = None


@dataclass
class Detection:
    """What the player shows in the provider picker."""

    id: str
    label: str
    available: bool
    path: Optional[str] = None
    models: list[str] = field(default_factory=list)
    supports_effort: bool = False
    efforts: list[str] = field(default_factory=list)
    # Why an installed provider is still unavailable. Shown verbatim in the UI —
    # a greyed-out row with no reason is a bug report waiting to happen.
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "available": self.available,
            "path": self.path,
            "models": self.models,
            "supportsEffort": self.supports_effort,
            "efforts": self.efforts,
            "reason": self.reason,
        }


def _err(code: str, message: str, fix: Optional[str] = None) -> dict:
    return {"type": "error", "code": code, "message": message, "fix": fix}


class Adapter:
    """Base class: build an argv, translate the CLI's JSONL into our events."""

    id: str = ""
    label: str = ""
    executable: str = ""
    models: tuple[str, ...] = ()
    supports_effort: bool = False
    # Set when a provider is installed but cannot be run safely for this
    # feature. Surfaced to the user instead of silently omitting the row.
    unavailable_reason: Optional[str] = None

    def which(self) -> Optional[str]:
        return shutil.which(self.executable)

    def detect(self) -> Detection:
        path = self.which()
        if path is None:
            return Detection(
                id=self.id, label=self.label, available=False,
                reason=f"{self.executable} was not found on your PATH.",
            )
        return Detection(
            id=self.id,
            label=self.label,
            available=self.unavailable_reason is None,
            path=path,
            models=list(self.models),
            supports_effort=self.supports_effort,
            efforts=list(EFFORT_LEVELS) if self.supports_effort else [],
            reason=self.unavailable_reason,
        )

    def build_argv(self, req: TurnRequest) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    def translate(self, obj: dict) -> Iterator[dict]:  # pragma: no cover
        raise NotImplementedError

    def observed_tools(self, obj: dict) -> Optional[list[str]]:
        """Return the CLI's reported tool list from its init event, if this line
        is one. Used by the fail-closed gate; None means "not an init line"."""
        return None


def _unsafe_tools(tools: list[str]) -> list[str]:
    return [t for t in tools if not t.startswith(SAFE_TOOL_PREFIX)]


def run_turn(
    adapter: Adapter,
    req: TurnRequest,
    *,
    cancel: Optional[threading.Event] = None,
) -> Iterator[dict]:
    """Run one turn, yielding normalized events until the process exits.

    Always terminates with exactly one ``done`` event, so the tab can close the
    stream on a single condition whatever happened.
    """
    cancel = cancel or threading.Event()
    detection = adapter.detect()
    if not detection.available:
        yield _err(
            "provider_unavailable",
            detection.reason or f"{adapter.label} is not available.",
            "Choose a different AI app in Settings → AI.",
        )
        yield {"type": "done", "ok": False}
        return

    argv = adapter.build_argv(req)
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,  # never let the CLI block waiting on input
            cwd=str(req.cwd) if req.cwd else None,
            text=True,
            bufsize=1,  # line-buffered: events reach the tab as they happen
            start_new_session=True,  # own process group, so cancel kills children
        )
    except OSError as e:
        yield _err("spawn_failed", f"Could not start {adapter.label}: {e}")
        yield {"type": "done", "ok": False}
        return

    def _kill() -> None:
        try:
            proc.kill()
        except OSError:
            pass

    # The watcher gets its OWN stop signal. Reusing the caller's `cancel` to
    # release it at the end makes a clean run indistinguishable from a
    # user-cancelled one, and every turn reports done:false.
    finished = threading.Event()

    def _watch() -> None:
        while not finished.wait(0.2):
            if cancel.is_set():
                _kill()
                return

    watcher = threading.Thread(target=_watch, daemon=True)
    watcher.start()

    ok = True
    gate_checked = False
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue  # non-JSON chatter on stdout is not fatal
            if not isinstance(obj, dict):
                continue

            # Fail closed: the CLI tells us what it actually ended up holding.
            # If that includes anything outside the OpenPod MCP surface, stop —
            # do not hand a podcast transcript an agent with a shell.
            if not gate_checked:
                tools = adapter.observed_tools(obj)
                if tools is not None:
                    gate_checked = True
                    unsafe = _unsafe_tools(tools)
                    if unsafe:
                        _kill()
                        yield _err(
                            "unsafe_tool_surface",
                            "Refusing to run: your AI app exposed tools OpenPod "
                            "cannot allow in a chat that reads podcast "
                            f"transcripts ({', '.join(sorted(unsafe)[:6])}).",
                            "This usually means a newer CLI added a tool OpenPod "
                            "does not know about yet. Please report it.",
                        )
                        ok = False
                        break

            for event in adapter.translate(obj):
                if event.get("type") == "error":
                    ok = False
                yield event
    except (OSError, ValueError) as e:
        ok = False
        yield _err("stream_failed", f"Lost the connection to {adapter.label}: {e}")
    finally:
        if cancel.is_set():
            _kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill()
        finished.set()  # release the watcher without faking a cancellation
        watcher.join(timeout=1)

    if cancel.is_set() and ok:
        yield {"type": "done", "ok": False}
        return
    if proc.returncode not in (0, None) and ok:
        stderr = ""
        try:
            stderr = (proc.stderr.read() or "").strip() if proc.stderr else ""
        except (OSError, ValueError):
            pass
        ok = False
        yield _err(
            "provider_failed",
            stderr.splitlines()[-1] if stderr else
            f"{adapter.label} exited with code {proc.returncode}.",
        )
    yield {"type": "done", "ok": ok}


def new_session_id() -> str:
    return str(uuid.uuid4())


# Registry is populated by the concrete adapter module.
from .cli_agent import ADAPTERS  # noqa: E402  (circular-free: cli_agent imports names above)


def get_adapter(provider_id: str) -> Optional[Adapter]:
    return ADAPTERS.get(provider_id)


def detect_all() -> list[dict]:
    """Every known provider, for the player's provider picker."""
    return [a.detect().to_dict() for a in ADAPTERS.values()]
