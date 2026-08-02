"""Adapters for locally-installed agent CLIs.

Each adapter turns a :class:`~openpod.providers.TurnRequest` into an argv and
translates that CLI's native JSONL into OpenPod's normalized event shape. The
argv flags and the event shapes below were established by running the real CLIs
and reading what came back — not from documentation — because several of them
behave differently than the docs suggest (see the sandbox notes in the package
docstring, and ``_CLAUDE_DOUBLE_TEXT`` below).
"""

from __future__ import annotations

from typing import Iterator, Optional

from . import (
    BUILTIN_DENY,
    Adapter,
    TurnRequest,
    new_session_id,
)

# With --include-partial-messages, Claude Code emits assistant text TWICE: once
# as stream_event/content_block_delta (token by token) and again as the complete
# `assistant` message when the block closes. Rendering both duplicates every
# reply. So: text comes from deltas only, tool calls from the complete message
# only (tool input is not usefully reconstructable from input_json_delta).
_CLAUDE_DOUBLE_TEXT = True


class ClaudeLocalAdapter(Adapter):
    """Claude Code, headless. The user's own subscription does the thinking."""

    id = "claude_local"
    label = "Claude Code"
    executable = "claude"
    # Aliases rather than pinned ids, so the CLI resolves the current model and
    # this list does not rot. The player shows these in the model picker.
    models = ("opus", "sonnet", "haiku")
    supports_effort = True  # --effort low|medium|high|xhigh|max

    def build_argv(self, req: TurnRequest) -> list[str]:
        argv = [
            self.executable,
            "-p", req.prompt,
            "--output-format", "stream-json",
            "--verbose",                 # required alongside stream-json
            "--include-partial-messages",  # token-level streaming into the tab
            # Drop the user's settings, hooks and plugins. Without this the
            # spawned agent inherits SessionStart hooks from unrelated projects,
            # which have been observed injecting instructions into the turn.
            "--setting-sources", "",
            # Only the MCP servers we pass. Without this the agent comes up
            # holding the user's Gmail / Drive / Calendar connections.
            "--strict-mcp-config",
        ]
        if req.mcp_config is not None:
            argv += ["--mcp-config", str(req.mcp_config)]
        # A denylist, not an allowlist: --allowedTools is an additive permission
        # grant and does NOT restrict — a run allowlisting one MCP tool still
        # called Bash and read a canary file off disk.
        argv += ["--disallowedTools", *BUILTIN_DENY]
        if req.model:
            argv += ["--model", req.model]
        if req.effort:
            argv += ["--effort", req.effort]
        if req.system:
            argv += ["--system-prompt", req.system]
        # First turn names the session; later turns resume it, so multi-turn
        # memory is the CLI's job rather than something we rebuild.
        if req.session_id:
            argv += ["--resume", req.session_id]
        else:
            argv += ["--session-id", new_session_id()]
        return argv

    def observed_tools(self, obj: dict) -> Optional[list[str]]:
        if obj.get("type") == "system" and obj.get("subtype") == "init":
            tools = obj.get("tools")
            return [str(t) for t in tools] if isinstance(tools, list) else []
        return None

    def translate(self, obj: dict) -> Iterator[dict]:
        kind = obj.get("type")

        if kind == "system" and obj.get("subtype") == "init":
            sid = obj.get("session_id")
            if sid:
                yield {"type": "session", "sessionId": str(sid)}
            return

        if kind == "stream_event":
            event = obj.get("event") or {}
            if event.get("type") == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    yield {"type": "text", "text": str(delta["text"])}
            return

        if kind == "assistant":
            for block in (obj.get("message") or {}).get("content") or []:
                btype = block.get("type")
                if btype == "text" and not _CLAUDE_DOUBLE_TEXT:
                    if block.get("text"):
                        yield {"type": "text", "text": str(block["text"])}
                elif btype == "tool_use":
                    yield {
                        "type": "tool_call",
                        "id": str(block.get("id") or ""),
                        "name": _strip_mcp_prefix(str(block.get("name") or "")),
                        "input": block.get("input") or {},
                    }
            return

        if kind == "user":
            for block in (obj.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_result":
                    yield {
                        "type": "tool_result",
                        "id": str(block.get("tool_use_id") or ""),
                        "ok": not block.get("is_error"),
                        "preview": _preview(block.get("content")),
                    }
            return

        if kind == "result" and obj.get("is_error"):
            yield {
                "type": "error",
                "code": "provider_error",
                "message": str(obj.get("result") or "The AI app reported an error."),
                "fix": None,
            }


class CodexLocalAdapter(Adapter):
    """Codex CLI, headless.

    The event translation below is verified against a real ``codex exec --json``
    run. What is *not* yet verified is a safe tool surface, and that is why this
    adapter reports itself unavailable.

    Codex's design centres a sandboxed shell that cannot be removed the way
    Claude Code's built-in tools can be denied by name. For a chat whose whole
    job is to read untrusted third-party podcast transcripts, an irremovable
    shell is the wrong shape: a hostile transcript reaching it is remote code
    execution on the listener's machine. Enabling this adapter means first
    proving, with the same canary test used for Claude Code, that a transcript
    cannot reach the shell — not merely that the sandbox is read-only, since
    reading is exactly the leak we care about.
    """

    id = "codex_local"
    label = "Codex"
    executable = "codex"
    models = ("gpt-5.1-codex", "gpt-5.1")
    supports_effort = True  # via -c model_reasoning_effort=<level>
    unavailable_reason = (
        "OpenPod can't yet confine Codex's shell, and this chat reads podcast "
        "transcripts it doesn't control. Use Claude Code for now."
    )

    def build_argv(self, req: TurnRequest) -> list[str]:
        argv = [self.executable, "exec", "--json", "--skip-git-repo-check"]
        if req.model:
            argv += ["-m", req.model]
        if req.effort:
            argv += ["-c", f'model_reasoning_effort="{req.effort}"']
        if req.session_id:
            # `codex exec resume <SESSION_ID> <PROMPT>`
            argv = [self.executable, "exec", "resume", req.session_id, *argv[2:]]
        argv.append(req.prompt)
        return argv

    def translate(self, obj: dict) -> Iterator[dict]:
        kind = obj.get("type")

        if kind == "thread.started":
            tid = obj.get("thread_id")
            if tid:
                yield {"type": "session", "sessionId": str(tid)}
            return

        # Codex reports completed items rather than token deltas, so a reply
        # arrives whole. The tab renders it the same way; it just lands at once.
        if kind == "item.completed":
            item = obj.get("item") or {}
            itype = item.get("type")
            if itype == "agent_message" and item.get("text"):
                yield {"type": "text", "text": str(item["text"])}
            elif itype in ("mcp_tool_call", "command_execution", "function_call"):
                yield {
                    "type": "tool_call",
                    "id": str(item.get("id") or ""),
                    "name": _strip_mcp_prefix(str(item.get("name") or itype)),
                    "input": item.get("arguments") or item.get("input") or {},
                }
            return

        if kind == "error":
            yield {
                "type": "error",
                "code": "provider_error",
                "message": str(obj.get("message") or "Codex reported an error."),
                "fix": None,
            }


def _strip_mcp_prefix(name: str) -> str:
    """`mcp__openpod__player_seek` -> `player_seek` — the tab labels action
    cards with the tool the user would recognize, not the transport's name."""
    if name.startswith("mcp__"):
        return name.split("__", 2)[-1]
    return name


def _preview(content: object, limit: int = 400) -> str:
    """A short, printable tool result for the thread. Never the whole payload —
    a transcript fetch would otherwise dump 40k words into the chat log."""
    if isinstance(content, list):
        parts = [
            str(b.get("text", "")) for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        content = " ".join(p for p in parts if p)
    text = str(content or "").strip()
    return text[:limit] + ("…" if len(text) > limit else "")


ADAPTERS: dict[str, Adapter] = {
    a.id: a for a in (ClaudeLocalAdapter(), CodexLocalAdapter())
}
