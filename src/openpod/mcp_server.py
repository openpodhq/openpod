"""OpenPod MCP server — the agent-native surface.

Exposes the four Stage 1 primitives (`catch`, `clip`, `export_timestamps`,
`search`) plus persona/follow helpers as MCP tools. The agent supplies context
and tokens and does the inference; OpenPod returns transcript, structure,
citations, and deep-links written into the user's workspace.

Run it:  ``openpod-mcp``  (or ``python -m openpod.mcp_server``)
Requires the ``mcp`` extra:  ``pip install 'openpod[mcp]'``.
"""

from __future__ import annotations

import os
from typing import Optional

from .config import Workspace


def _workspace() -> Workspace:
    return Workspace(os.environ.get("OPENPOD_HOME"))


def build_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:  # pragma: no cover - depends on env
        raise RuntimeError(
            "The MCP server needs the 'mcp' package. Install with:\n"
            "    pip install 'openpod[mcp]'"
        ) from e

    mcp = FastMCP("openpod")

    @mcp.tool()
    def catch(link: str, kind: Optional[str] = None,
              transcript_path: Optional[str] = None, ideas: int = 8) -> dict:
        """Ingest a podcast/RSS/YouTube link (or local file) into the local
        library: transcribe, extract key ideas + a navigable TOC, and write
        transcript.json / ideas.md / briefing.md. Returns the entry id, key
        ideas with deep-links, and the TOC so you can author the briefing."""
        from .catch import catch as _catch

        r = _catch(link, workspace=_workspace(), kind=kind,
                   transcript_path=transcript_path, k_ideas=ideas)
        return {
            "entry_id": r.entry_id,
            "title": r.source.title,
            "show": r.source.show,
            "source_kind": r.source.kind,
            "transcript_cues": len(r.transcript),
            "transcript_source": r.transcript.source,
            "artifacts_dir": str(r.entry.dir),
            "ideas": [i.to_dict() for i in r.ideas],
            "toc": [i.to_dict() for i in r.toc],
        }

    @mcp.tool()
    def search(query: str, limit: int = 10, semantic: bool = True) -> list[dict]:
        """Search across the whole local library (keyword + local semantic
        re-rank). Returns ranked cues with show/episode, timestamp and a
        deep-link to the exact moment."""
        from .search import search as _search

        hits = _search(query, workspace=_workspace(), limit=limit, semantic=semantic)
        return [h.to_dict() for h in hits]

    @mcp.tool()
    def export_timestamps(entry_id: str, fmt: str = "json",
                          segments: int = 12) -> str:
        """Emit a caught episode's timed segments and deep-links (json or
        markdown) for hand-off to another tool or player."""
        from .exports import export_timestamps as _export

        return _export(entry_id, workspace=_workspace(), fmt=fmt, segments=segments)

    @mcp.tool()
    def clip(entry_id: str, start: float, end: float, snap: bool = True) -> dict:
        """Cut a local, user-owned clip from a caught episode (snapping to
        sentence boundaries) and generate a shareable deep-link card. Requires
        ffmpeg. Does not publish or re-host anything."""
        from .clip import clip as _clip

        r = _clip(entry_id, start, end, workspace=_workspace(), snap=snap)
        return {
            "path": str(r.path),
            "start": r.start,
            "end": r.end,
            "quote": r.quote,
            "deeplink": r.deeplink,
            "card_path": str(r.card_path) if r.card_path else None,
        }

    @mcp.tool()
    def get_briefing(entry_id: str) -> dict:
        """Return the raw artifacts for a caught episode (briefing scaffold,
        ideas, and the full transcript) so you can write the personalized,
        cited briefing."""
        from .library import Library

        entry = Library(_workspace()).get(entry_id)
        if entry is None:
            raise ValueError(f"no caught episode with id {entry_id!r}")
        transcript = entry.read_transcript()
        return {
            "entry_id": entry.entry_id,
            "briefing_scaffold": entry.briefing_path.read_text(encoding="utf-8")
            if entry.briefing_path.exists() else None,
            "ideas": entry.ideas_path.read_text(encoding="utf-8")
            if entry.ideas_path.exists() else None,
            "transcript": transcript.to_dict() if transcript else None,
        }

    @mcp.tool()
    def persona() -> str:
        """Read the local persona.md the reader owns, to personalize briefings
        and triage. Returns guidance if it hasn't been created yet."""
        from .persona import Persona

        p = Persona(_workspace())
        return p.read() or (
            "No persona yet. Ask the user the interview questions and write "
            "persona.md, or run `openpod persona init`."
        )

    @mcp.tool()
    def follow(url: str, title: Optional[str] = None) -> dict:
        """Follow a podcast RSS feed or YouTube channel (local list)."""
        from .follows import Follows

        f = Follows(_workspace()).add(url, title=title)
        return {"url": f.url, "kind": f.kind, "title": f.title}

    @mcp.tool()
    def digest(per_feed: int = 5) -> list[dict]:
        """What's new across the followed feeds (polled locally)."""
        from .follows import Follows

        items = Follows(_workspace()).poll(per_feed=per_feed)
        return [vars(i) for i in items]

    return mcp


def main() -> None:
    build_server().run()


if __name__ == "__main__":  # pragma: no cover
    main()
