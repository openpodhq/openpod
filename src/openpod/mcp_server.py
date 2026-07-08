"""OpenPod MCP server — the agent-native surface.

Exposes the Stage 1 primitives (`catch`, `clip`, `export_timestamps`,
`search`, plus persona / follow / notes / import helpers) as MCP tools, and
the packaged **skills** — Catch Me Up, Set Up My Persona, Bring In My World,
… — as MCP prompts, so installing OpenPod surfaces the features alongside the
tools. The agent supplies context and tokens and does the inference; OpenPod
returns transcript, structure, citations, and deep-links written into the
user's workspace.

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
              transcript_path: Optional[str] = None, ideas: int = 8,
              confirmed: bool = False) -> dict:
        """Ingest a podcast/RSS/YouTube/Spotify/Apple link (or local file)
        into the local library: transcribe, extract key ideas + a navigable
        TOC, and write transcript.json / ideas.md / briefing.md. Returns the
        entry id, key ideas with deep-links, and the TOC so you can author
        the briefing.

        If the result has `error: "needs_confirmation"`, OpenPod matched the
        episode fuzzily — show the candidate to the user, and only after they
        confirm call catch again with confirmed=true. Never present content
        from an unconfirmed match. `error: "unresolved_link"` means the
        deterministic paths ran out: you may resolve it yourself (the `tried`
        list shows what not to repeat), and prefer passing the resolved RSS
        feed URL back into catch so the result is recorded."""
        from .catch import catch as _catch
        from .cli import _catch_dict
        from .errors import OpenPodError
        from .persona import Persona

        ws = _workspace()
        try:
            r = _catch(link, workspace=ws, kind=kind,
                       transcript_path=transcript_path, k_ideas=ideas,
                       confirmed=confirmed)
        except OpenPodError as e:
            # Deny-and-continue: structured explanation, not a bare failure.
            return e.to_dict()
        result = _catch_dict(r)
        if not Persona(ws).exists():
            result["next_step"] = (
                "No persona.md yet, so the briefing you author will be "
                "generic. Offer the user the 'Set Up My Persona' skill "
                "(2 minutes) — never block the briefing on it."
            )
        return result

    @mcp.tool()
    def search(query: str, limit: int = 10, semantic: bool = True) -> list[dict]:
        """Search across the whole local library (keyword + local semantic
        re-rank). Each hit carries an anchor ladder — offer the user every
        rung that exists, labeled: `chapter_*` is the creator's own chapter
        ("take me to the topic"), `segment_*` is the detected beat where the
        idea starts being articulated (usually the best default citation),
        and `deeplink` is the exact cue where the words were said. Links are
        cheap; say briefly what each one lands on."""
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
    def clip(entry_id: str, start: float, end: float, snap: bool = True,
             video: Optional[bool] = None) -> dict:
        """Cut a local, user-owned clip from a caught episode (snapping to
        sentence boundaries) and generate a shareable deep-link card. Requires
        ffmpeg. Does not publish or re-host anything.

        Video is preserved by default when the source has it (video=None);
        video=false forces audio-only. Check `has_video` and surface
        `capability_note` to the user BEFORE delivering — never hand over an
        audio-only clip as if it were the video they asked for. Source media
        is cached under .openpod/media/ and reused across clips."""
        from .clip import clip as _clip

        r = _clip(entry_id, start, end, workspace=_workspace(), snap=snap,
                  video=video)
        return {
            "path": str(r.path),
            "start": r.start,
            "end": r.end,
            "quote": r.quote,
            "deeplink": r.deeplink,
            "has_video": r.has_video,
            "capability_note": r.capability_note,
            "card_path": str(r.card_path) if r.card_path else None,
            "card_png_path": str(r.card_png_path) if r.card_png_path else None,
        }

    @mcp.tool()
    def playback_link(entry_id: str, seconds: float,
                      app: Optional[str] = None) -> dict:
        """Build the best "jump to this moment" link for a caught episode,
        honoring the user's preferred playback app (settings) and the
        platform they originally pasted. The result says honestly what the
        link can do: `timestamp_supported: false` means it opens the episode,
        not the moment — tell the user, don't imply precision. Apps:
        youtube, spotify, apple, overcast, podcast (open enclosure)."""
        from .crosswalk import Crosswalk
        from .deeplink import build_link
        from .library import Library
        from .models import SourceRef

        ws = _workspace()
        entry = Library(ws).get(entry_id)
        if entry is None:
            raise ValueError(f"no caught episode with id {entry_id!r}")
        meta = entry.read_meta()
        source = entry.source()
        origin = SourceRef.from_dict(meta["origin"]) if meta.get("origin") else None
        identity = None
        if meta.get("episode_key"):
            identity = Crosswalk(ws).get(meta["episode_key"])
        preferred = ws.load_settings().get("preferred_playback_app")
        r = build_link(source, seconds, identity=identity, app=app,
                       origin=origin, preferred_app=preferred)
        return r.to_dict()

    @mcp.tool()
    def set_preferred_app(app: Optional[str] = None) -> dict:
        """Get or set the user's preferred playback app (youtube, spotify,
        apple, overcast, podcast). Call with no argument to read the current
        setting. Only set it at the user's explicit request."""
        from .deeplink import APP_CAPABILITIES

        ws = _workspace()
        settings = ws.load_settings()
        if app:
            if app not in APP_CAPABILITIES:
                raise ValueError(
                    f"unknown app {app!r}; choose from {sorted(APP_CAPABILITIES)}")
            settings["preferred_playback_app"] = app
            ws.save_settings(settings)
        current = settings.get("preferred_playback_app")
        caps = APP_CAPABILITIES.get(current or "", {})
        return {"preferred_playback_app": current,
                "timestamp_support": caps.get("timestamp"),
                "path": str(ws.settings_file)}

    @mcp.tool()
    def get_briefing(entry_id: str) -> dict:
        """Return the raw artifacts for a caught episode (briefing scaffold,
        ideas, and the full transcript) so you can write the personalized,
        cited briefing."""
        from .library import Library

        entry = Library(_workspace()).get(entry_id)
        if entry is None:
            raise ValueError(
                f"no caught episode with id {entry_id!r} — "
                "list_entries shows what's in the library; catch adds to it"
            )
        transcript = entry.read_transcript()
        return {
            "entry_id": entry.entry_id,
            "briefing_scaffold": entry.briefing_path.read_text(encoding="utf-8")
            if entry.briefing_path.exists() else None,
            "ideas": entry.ideas_path.read_text(encoding="utf-8")
            if entry.ideas_path.exists() else None,
            "notes": entry.notes_path.read_text(encoding="utf-8")
            if entry.notes_path.exists() else None,
            "transcript": transcript.to_dict() if transcript else None,
            "paths": {
                "dir": str(entry.dir),
                "briefing": str(entry.briefing_path),
                "ideas": str(entry.ideas_path),
                "notes": str(entry.notes_path),
            },
        }

    @mcp.tool()
    def persona() -> dict:
        """Read the local persona.md the reader owns, to personalize briefings
        and triage. Returns the path so writes can be reported to the user."""
        from .persona import Persona

        p = Persona(_workspace())
        return {
            "path": str(p.path),
            "exists": p.exists(),
            "content": p.read() or (
                "No persona yet. Ask the user the interview questions and "
                "write persona.md, or run `openpod persona init`."
            ),
        }

    @mcp.tool()
    def persona_scan(extra_roots: Optional[list[str]] = None) -> dict:
        """Read-only sweep of the workspace for persona evidence: project
        folders (recency-ordered), markdown doc titles, CLAUDE.md headings,
        library themes, follows. Use it BEFORE the persona interview and turn
        the evidence into multi-select guesses the user confirms — the user
        picks, they don't type. Pass extra_roots only for folders the user
        explicitly offered (e.g. their projects directory). Also mine your
        own memory/context for guesses; that's your asset, not OpenPod's."""
        from .scan import scan_workspace

        return scan_workspace(_workspace(), extra_roots=extra_roots)

    @mcp.tool()
    def follow(url: str, title: Optional[str] = None) -> dict:
        """Follow a podcast RSS feed or YouTube channel (local list)."""
        from .follows import Follows

        ws = _workspace()
        f = Follows(ws).add(url, title=title)
        return {"url": f.url, "kind": f.kind, "title": f.title,
                "path": str(ws.follows_file)}

    @mcp.tool()
    def digest(per_feed: int = 5) -> list[dict]:
        """What's new across the followed feeds (polled locally). Items with
        `in_rotation: false` come from shows the user follows but has never
        caught — the discovery pool. Don't bury those under familiar shows:
        when one matches the user's interests, surface it and offer a cheap
        trial (catch it, read the briefing or the one matching beat) before
        they commit to a full episode. A false positive costs two minutes;
        a missed gem costs the product's whole promise."""
        from .follows import Follows

        items = Follows(_workspace()).poll(per_feed=per_feed)
        return [vars(i) for i in items]

    @mcp.tool()
    def list_entries() -> list[dict]:
        """List the caught episodes in the local library, with their artifact
        directories."""
        from .library import Library

        return [{"entry_id": e.entry_id, "show": e.show(), "title": e.title(),
                 "dir": str(e.dir)} for e in Library(_workspace())]

    @mcp.tool()
    def append_note(entry_id: str, note: str) -> dict:
        """Append a note to an episode's notes.md **at the user's request**.
        notes.md is the user's voice — never rewrite it, only append what they
        asked to record."""
        from .library import Library

        entry = Library(_workspace()).get(entry_id)
        if entry is None:
            raise ValueError(
                f"no caught episode with id {entry_id!r} — "
                "list_entries shows what's in the library"
            )
        entry.append_note(note)
        return {"entry_id": entry_id, "path": str(entry.notes_path)}

    @mcp.tool()
    def import_opml(path: str, label: Optional[str] = None) -> dict:
        """Import podcast subscriptions from an OPML file the user exported
        (opt-in). Stages the raw file in imports/, merges feeds into
        follows.yaml with source-provenance tags, and refreshes the persona's
        machine-owned '## Imported interests (opt-in)' block — the user's own
        sections are never touched."""
        from .imports import import_opml as _import

        return _import(path, workspace=_workspace(), label=label).to_dict()

    @mcp.tool()
    def push_to_player() -> dict:
        """Push local artifacts to a paired player (opt-in, needs `sync login`):
        the follow list, the openpod-entry ↔ episode-key map, §6.4 segment
        recommendations, and gzipped transcripts. Returns per-step counts. Some
        steps need OpenPod Pro; a 402 is raised as an error the user can act on.
        Nothing runs unless the user has logged in — this is not automatic."""
        from . import sync as _sync

        ws = _workspace()
        return {
            "follows": _sync.push_follows(ws),
            "entry_map": _sync.push_entry_map(ws),
            "segments": _sync.push_segments(ws),
            "transcripts": _sync.push_transcripts(ws),
        }

    @mcp.tool()
    def pull_from_player(heard: bool = False) -> dict:
        """Pull player-side signal back into the local library (opt-in). Default
        pulls bookmarks into each episode's notes.md inside a machine-owned
        fenced block (the user's prose is preserved verbatim). With heard=true,
        pulls heard cues into per-episode listened.json and merges them so the
        offline search covers heard content. Returns paths written and any
        bookmarks/cues whose episode isn't caught locally yet."""
        from . import sync as _sync

        ws = _workspace()
        return _sync.pull_heard(ws) if heard else _sync.pull_bookmarks(ws)

    # -- skills: the packaged features, exposed as MCP prompts --------------- #

    from .skills import list_skills

    for skill in list_skills():
        def _prompt(s=skill) -> str:
            return s.body

        mcp.prompt(name=skill.slug, description=skill.description)(_prompt)

    return mcp


def main() -> None:
    build_server().run()


if __name__ == "__main__":  # pragma: no cover
    main()
