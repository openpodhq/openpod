"""Anti-drift: the vendored player manifest is a *copy*, and copies rot.

``src/openpod/player_manifest.json`` is a byte-for-byte copy of the player's
generated ``docs/agent-manifest.json``. Every entry becomes one ``player_*``
MCP tool (see ``mcp_server.build_server``), so a stale copy silently costs
agents whole tools — the failure is invisible until someone asks why a tool
the player implements isn't callable. That is exactly how ``mark_map_range``
and ``set_skip_heard`` went missing.

The player lives in a separate, closed repo, so engine CI cannot check out the
source of truth. The guard is therefore two-layered:

* ``test_every_tool_becomes_an_mcp_tool`` / ``test_manifest_shape`` run
  everywhere, including CI, and catch a malformed or half-applied sync.
* ``test_copy_matches_player_repo`` is the real parity check. It runs whenever
  the player repo is present — which is the developer machine where both repos
  sit side by side, and where drift is actually introduced. Elsewhere it skips
  loudly rather than passing quietly.

Re-sync with (from the OpenPod root, after ``pnpm --filter @openpod/app
gen:agent-docs`` in the player)::

    cp player/docs/agent-manifest.json openpod/src/openpod/player_manifest.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from openpod.bridge import MANIFEST, _MANIFEST_PATH

# Set this when the player repo lives somewhere other than the sibling default.
PLAYER_REPO_ENV = "OPENPOD_PLAYER_REPO"
# Set this in an environment that *must* have the player checked out, so a
# missing repo fails instead of skipping.
REQUIRE_ENV = "OPENPOD_REQUIRE_PLAYER_PARITY"

# The fields `mcp_server._player_description` reads off every spec.
REQUIRED_FIELDS = ("name", "summary", "params", "returns")


def _player_manifest_path() -> Path | None:
    """The player's generated manifest, if this machine has the player repo."""
    override = os.environ.get(PLAYER_REPO_ENV)
    roots = (
        [Path(override)]
        if override
        # tests/ -> repo root -> OpenPod root -> player/
        else [_MANIFEST_PATH.parents[3] / "player"]
    )
    for root in roots:
        candidate = root / "docs" / "agent-manifest.json"
        if candidate.is_file():
            return candidate
    return None


def test_manifest_shape() -> None:
    """Every spec carries what the tool description is built from."""
    raw = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert raw["actionsVersion"] == 1
    assert raw["tools"], "manifest has no tools — a truncated or failed sync"

    for spec in raw["tools"]:
        missing = [f for f in REQUIRED_FIELDS if not spec.get(f)]
        assert not missing, f"{spec.get('name', '<unnamed>')} is missing {missing}"

    names = [t["name"] for t in raw["tools"]]
    assert len(names) == len(set(names)), "duplicate tool names in the manifest"


def test_every_tool_becomes_an_mcp_tool() -> None:
    """The consumer contract: one ``player_*`` MCP tool per manifest entry.

    This is the step that made the drift expensive — a name absent here is a
    tool no agent can call, however well the player implements it.
    """
    pytest.importorskip("mcp", reason="the MCP server needs the 'mcp' extra")
    import asyncio

    from openpod.mcp_server import build_server

    tools = asyncio.run(build_server().list_tools())
    exposed = {t.name for t in tools if t.name.startswith("player_")}
    expected = {f"player_{name}" for name in MANIFEST}
    assert exposed == expected


def test_copy_matches_player_repo() -> None:
    """The vendored copy is byte-identical to what the player generates."""
    source = _player_manifest_path()
    if source is None:
        if os.environ.get(REQUIRE_ENV):
            pytest.fail(
                f"{REQUIRE_ENV} is set but the player repo was not found. "
                f"Point {PLAYER_REPO_ENV} at it."
            )
        pytest.skip(
            "player repo not found — set "
            f"{PLAYER_REPO_ENV} to check manifest parity from here"
        )

    expected = source.read_bytes()
    actual = _MANIFEST_PATH.read_bytes()
    if actual == expected:
        return

    # A name-level diff makes the failure readable; a byte diff of a 16KB JSON
    # blob does not.
    def names(blob: bytes) -> list[str]:
        return [t["name"] for t in json.loads(blob)["tools"]]

    theirs, ours = names(expected), names(actual)
    detail = ""
    if missing := [n for n in theirs if n not in ours]:
        detail += f"\n  missing from the engine copy: {missing}"
    if extra := [n for n in ours if n not in theirs]:
        detail += f"\n  stale in the engine copy: {extra}"
    if not detail:
        detail = "\n  same tools — the specs or formatting differ."

    pytest.fail(
        f"{_MANIFEST_PATH.name} has drifted from {source}.{detail}\n"
        f"Regenerate and re-sync:\n"
        f"  pnpm --filter @openpod/app gen:agent-docs   # in the player repo\n"
        f"  cp {source} {_MANIFEST_PATH}"
    )
