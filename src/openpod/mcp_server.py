"""OpenPod MCP server — the agent-native surface.

Exposes the Stage 1 primitives (`catch`, `clip`, `export_timestamps`,
`search`, plus persona / follow / notes / import helpers) as MCP tools, and
the packaged **skills** — Catch Me Up, Set Up My Persona, Bring In My World,
… — as MCP prompts, so installing OpenPod surfaces the features alongside the
tools. The agent supplies context and tokens and does the inference; OpenPod
returns transcript, structure, citations, and deep-links written into the
user's workspace.

Run it:  ``openpod-mcp``  (or ``python -m openpod.mcp_server``)
Requires the ``mcp`` extra:  ``pip install "openpod[mcp]"``.
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
            '    pip install "openpod[mcp]"'
        ) from e

    mcp = FastMCP("openpod")

    @mcp.tool()
    def catch(link: str, kind: Optional[str] = None,
              transcript_path: Optional[str] = None, ideas: int = 8,
              confirmed: bool = False, asr: str = "auto") -> dict:
        """Ingest a podcast/RSS/YouTube/Spotify/Apple link (or local file)
        into the local library: transcribe, extract key ideas + a navigable
        TOC, and write transcript.json / transcript.md / ideas.md /
        briefing.md. transcript.md is the human-readable view — reflowed
        paragraphs, chaptered, a deep-link badge per paragraph — point the
        user at it when they want to skim rather than ask. Returns the
        entry id, key ideas with deep-links, and the TOC so you can author
        the briefing.

        If the result has `error: "needs_confirmation"`, OpenPod matched the
        episode fuzzily — show the candidate to the user, and only after they
        confirm call catch again with confirmed=true. Never present content
        from an unconfirmed match. `error: "unresolved_link"` means the
        deterministic paths ran out: you may resolve it yourself (the `tried`
        list shows what not to repeat), and prefer passing the resolved RSS
        feed URL back into catch so the result is recorded.

        Short local transcriptions run automatically — when the result
        carries `transcript_notes`, relay it to the user (it says why ASR
        ran, that it's local compute with no token/API cost, and whether a
        later re-catch can upgrade to platform captions). You only get
        `error: "captions_unavailable"` when the transcription would be
        LONG and captions are merely throttled (YouTube 429) — then give
        the user the choice from `options`: wait a few minutes and re-catch
        (free), or re-call with asr="now" (the `asr_estimate` prices it)."""
        from .catch import catch as _catch
        from .cli import _catch_dict
        from .errors import OpenPodError
        from .persona import Persona

        ws = _workspace()
        try:
            r = _catch(link, workspace=ws, kind=kind,
                       transcript_path=transcript_path, k_ideas=ideas,
                       confirmed=confirmed, asr=asr)
        except OpenPodError as e:
            # Deny-and-continue: structured explanation, not a bare failure.
            return e.to_dict()
        result = _catch_dict(r)
        _p = Persona(ws)
        if not (_p.exists() or _p.global_exists()):
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
             video: Optional[bool] = None, captions: Optional[str] = None,
             lang: Optional[str] = None, captions_file: Optional[str] = None,
             word_level: bool = False, label: Optional[bool] = None,
             label_text: Optional[str] = None, hook: Optional[str] = None,
             aspect: Optional[str] = None, style: Optional[str] = None,
             out_dir: Optional[str] = None) -> dict:
        """Cut a local, user-owned clip from a caught episode (snapping to
        sentence boundaries) and generate a shareable deep-link card. Requires
        ffmpeg. Does not publish or re-host anything.

        THE TWO-ARTIFACT CONTRACT: the file under the library is the clean
        master and is NEVER modified. Presentation — captions
        ('off'|'soft'|'burn'), a speaker name-plate, working-folder copies —
        are derivatives; burned files land only in out_dir (or the
        clip.export_dir setting). Never burn text into the library master
        yourself; use these parameters. Labels come from the episode's
        structured speakers (label=true) or explicit label_text — never from
        your own memory of who was speaking. If `captions.translation_needed`
        is set, translate the sidecar line-by-line (keep timings), then
        re-verify with the captions tool before any burn.

        Video is preserved by default when the source has it (video=None);
        video=false forces audio-only. Check `has_video` and surface
        `capability_note` to the user BEFORE delivering.

        `aspect` shapes the EXPORT derivative only (vertical 9:16 for
        TikTok/Reels/Shorts, square 1:1, wide 16:9); the master keeps source
        dimensions. Caption styling comes from clip.caption_style settings.

        `style` picks the burned-caption rendering: plain | keyword |
        marker | karaoke. For keyword/marker YOU choose the one word that
        carries each line and mark it `*word*` in the captions_file — same
        per-line contract as ‖ forced breaks; never guess a keyword list,
        especially for RTL text. Karaoke lights words as spoken and needs
        word_level=True (falls back to keyword styling with a note).

        FIRST USE: this workspace's clip defaults are the USER's decisions,
        not yours. A burn on an unconfigured workspace is REFUSED — the
        result is `{error: "needs_decision", first_use: …}` and no file is
        produced. A soft/off cut succeeds but carries the same questions as
        `first_use`. Either way: ask the questions ONCE, in one message, as
        multiple choice (never a form, never prose, never answered on the
        user's behalf), save each answer via the settings tool, set
        clip.setup_done=true only after the user has picked or explicitly
        skipped (skipping keeps defaults — never ask twice), then re-run.
        Until it's answered, don't silently pick presentation defaults like
        hidden sidecar captions when the user asked for a social clip."""
        from .clip import clip as _clip
        from .errors import OpenPodError

        try:
            r = _clip(entry_id, start, end, workspace=_workspace(), snap=snap,
                      video=video, captions=captions, lang=lang,
                      captions_file=captions_file, word_level=word_level,
                      label=label, label_text=label_text, hook=hook,
                      aspect=aspect, style=style, out_dir=out_dir)
        except OpenPodError as e:
            # Structured refusals (needs_decision etc.) are data the agent
            # branches on, not stack traces.
            return e.to_dict()
        return {
            "path": str(r.path),
            "start": r.start,
            "end": r.end,
            "quote": r.quote,
            "deeplink": r.deeplink,
            "has_video": r.has_video,
            "capability_note": r.capability_note,
            "captions_path": str(r.captions_path) if r.captions_path else None,
            "captions": r.captions,
            "label": r.label,
            "aspect": r.aspect,
            "first_use": r.first_use,
            "export_dir": str(r.export_dir) if r.export_dir else None,
            "export_paths": [str(p) for p in r.export_paths],
            "card_path": str(r.card_path) if r.card_path else None,
            "card_png_path": str(r.card_png_path) if r.card_png_path else None,
        }

    @mcp.tool()
    def captions(entry_id: str, start: float, end: float,
                 lang: Optional[str] = None,
                 verify_path: Optional[str] = None) -> dict:
        """Export a caption sidecar (.srt) for a clip window, generated from
        the transcript with a coverage report — or, with verify_path, check
        an edited/translated caption file against the transcript window.

        RULE: caption lines come from the transcript, never hand-summarized;
        after translating, keep every line and its timings, and re-run this
        tool with verify_path — a failed coverage report (ok=false, gaps
        listed) means spoken content was dropped and must be restored before
        any burn-in."""
        from pathlib import Path as _P

        from .captions import export_captions, parse_srt, verify_coverage
        from .library import Library

        entry = Library(_workspace()).get(entry_id)
        if entry is None:
            raise ValueError(f"no caught episode with id {entry_id!r}")
        if verify_path:
            transcript = entry.read_transcript()
            lines = parse_srt(_P(verify_path).read_text(encoding="utf-8"))
            return verify_coverage(transcript, start, end, lines).to_dict()
        return export_captions(entry, start, end, lang=lang).to_dict()

    @mcp.tool()
    def settings(key: Optional[str] = None, value: Optional[str] = None) -> dict:
        """Read or change settings.yaml. No args = full effective settings
        (defaults merged). key alone reads one dotted key; key + value sets
        it — only at the user's explicit request. Documented keys include
        locale.preferred_language, clip.captions (off|soft|burn),
        clip.export_dir, clip.label_template, preferred_playback_app."""
        ws = _workspace()
        if key and value is not None:
            v: object = value
            if value in ("true", "false"):
                v = value == "true"
            ws.set_setting(key, v)
            return {key: v, "path": str(ws.settings_file)}
        if key:
            return {key: ws.get_setting(key)}
        return ws.effective_settings()

    @mcp.tool()
    def doctor(fix: bool = False) -> dict:
        """Health report: ffmpeg capabilities (can this machine burn
        captions/labels at all?), settings file status, and library hygiene —
        foreign files (venvs, scripts, previews) that don't belong in the
        corpus. fix=true quarantines them under library/_scratch/ (moved,
        never deleted). Run this BEFORE promising burned-in text, and never
        write non-openpod files under .openpod/library/ yourself — use
        clip.export_dir for working files."""
        from .doctor import check

        return check(_workspace(), fix=fix)

    @mcp.tool()
    def playback_link(entry_id: str, seconds: float,
                      app: Optional[str] = None) -> dict:
        """Build the best "jump to this moment" link for a caught episode,
        honoring the user's preferred playback app (settings) and the
        platform they originally pasted. The result says honestly what the
        link can do: `timestamp_supported: false` means it opens the episode,
        not the moment — tell the user, don't imply precision. Apps: openpod
        (the default — our own player, keeps the listener in OpenPod), youtube,
        spotify, apple, overcast, podcast (raw enclosure — opens the OS default
        podcast app; explicit opt-in only)."""
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
        """Get or set the user's preferred playback app (openpod, youtube,
        spotify, apple, overcast, podcast). Leaving it unset keeps the default,
        openpod (our own player). Call with no argument to read the current
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
            "summary": entry.read_summary(body_only=True),
            "transcript": transcript.to_dict() if transcript else None,
            "paths": {
                "dir": str(entry.dir),
                "briefing": str(entry.briefing_path),
                "ideas": str(entry.ideas_path),
                "notes": str(entry.notes_path),
                "summary": str(entry.summary_path),
            },
        }

    @mcp.tool()
    def save_summary(entry_id: str, markdown: str, append: bool = False) -> dict:
        """Save the episode's summary — the durable, cross-session memory
        record — to summary.md in the local library.

        Author it from the *session*, not just the transcript: what the user
        engaged with, asked about, disagreed with, or plans to act on;
        exclude the parts that didn't matter to them. Cite moments with the
        deep-links from the catch/search results so any future session can
        jump to the source. Keep it compact — it will be loaded as context
        by future sessions (and later synced across devices), so every
        paragraph should earn its tokens. Structure, language, and depth are
        yours and the user's to choose — OpenPod only owns the file's
        location and identity frontmatter.

        append=true adds this session's take after the existing body (e.g.
        as a dated section) instead of rewriting the distillation. notes.md
        remains the user's own voice — never move it in here."""
        from .library import Library

        entry = Library(_workspace()).get(entry_id)
        if entry is None:
            raise ValueError(
                f"no caught episode with id {entry_id!r} — "
                "list_entries shows what's in the library")
        path = entry.write_summary(markdown, append=append)
        return {"entry_id": entry_id, "path": str(path),
                "revisions": _summary_revisions(entry)}

    @mcp.tool()
    def recall_summaries(query: Optional[str] = None, limit: int = 20) -> list[dict]:
        """The cross-session recall surface: every episode summary in the
        library (newest first), with an excerpt. Call this at the start of a
        podcast-related session to load what past sessions already learned —
        it's the difference between continuing a conversation and starting
        over. `query` filters by show/title/summary text (simple substring;
        use `search` for transcript-level search)."""
        from .library import Library

        out = []
        for entry in Library(_workspace()):
            body = entry.read_summary(body_only=True)
            if not body:
                continue
            if query:
                hay = " ".join([entry.show(), entry.title(), body]).lower()
                if query.lower() not in hay:
                    continue
            full = entry.read_summary() or ""
            import re as _re
            m = _re.search(r"^updated_at:\s*(\S+)", full, _re.M)
            out.append({
                "entry_id": entry.entry_id,
                "show": entry.show(),
                "title": entry.title(),
                "updated_at": m.group(1) if m else None,
                "excerpt": body.strip()[:400],
                "path": str(entry.summary_path),
            })
        out.sort(key=lambda d: d["updated_at"] or "", reverse=True)
        return out[:limit]

    def _summary_revisions(entry) -> int:
        import re as _re

        full = entry.read_summary() or ""
        m = _re.search(r"^revisions:\s*(\d+)", full, _re.M)
        return int(m.group(1)) if m else 0

    @mcp.tool()
    def persona() -> dict:
        """Read the user-owned persona — BOTH layers. `global` is who the
        user is everywhere (role, language, style, standing filters:
        ~/.openpod/persona.md); `workspace` is what this folder is for
        (project focus, local interests, the derived block). Identity and
        language lean global; relevance decisions lean workspace.

        Route writes by kind: "I prefer Hebrew" → global; "this project is
        about X" → workspace. When `split_proposal` is present, an older
        single-file persona predates the global layer — offer the split as a
        MULTIPLE-CHOICE confirmation (list the proposed sections, the user
        picks/deselects, then call persona_split). Never make the user
        write prose to organize system files, and never split without
        their confirmation."""
        from .persona import Persona

        p = Persona(_workspace())
        layers = p.read_layers()
        if not layers["workspace"]["exists"] and not layers["global"]["exists"]:
            layers["next_step"] = (
                "No persona yet. Run the 'Set Up My Persona' skill: scan for "
                "evidence, then multi-select questions — the user picks, "
                "they don't type.")
        proposal = p.propose_split()
        if proposal is not None:
            layers["split_proposal"] = proposal
        # Back-compat keys (pre-two-layer consumers read these).
        layers["path"] = layers["workspace"]["path"]
        layers["exists"] = (layers["workspace"]["exists"]
                            or layers["global"]["exists"])
        merged = []
        if layers["global"]["content"]:
            merged.append(layers["global"]["content"])
        if layers["workspace"]["content"]:
            merged.append(layers["workspace"]["content"])
        layers["content"] = "\n\n".join(merged) or None
        return layers

    @mcp.tool()
    def persona_split(to_global: list[str]) -> dict:
        """Apply a persona split: move the named `## ` sections from the
        workspace persona.md into the global one (~/.openpod/persona.md).
        Call ONLY after the user confirmed the selection from the
        split_proposal via multiple choice — never on your own judgment.
        Machine-owned sections are refused."""
        from .persona import Persona

        return Persona(_workspace()).apply_split(to_global)

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

    # -- player control: operate the browser player via the local bridge ----- #
    # Generated from the checked-in manifest (a copy of app/src/agent/manifest).
    # Each tool forwards a PlayerAction to a running `openpod player-bridge`,
    # which relays it to the connected tab (or queues durable state acts). Args
    # go in a single `args` object matching the tool's documented params.

    from .bridge import MANIFEST as _player_manifest, bridge_call

    def _player_description(spec: dict) -> str:
        lines = [spec["summary"], "",
                 f"Params (pass as `args`): {spec['params']}",
                 f"Returns: {spec['returns']}"]
        if spec.get("session"):
            lines.append("Needs a live player tab — cannot be queued.")
        elif spec.get("queueable"):
            lines.append("Runs now if a tab is connected; otherwise queued until one is.")
        return "\n".join(lines)

    def _make_player_tool(action_type: str):
        def player_tool(args: Optional[dict] = None) -> dict:
            return bridge_call(_workspace(), {"type": action_type, **(args or {})})

        return player_tool

    for _name, _spec in _player_manifest.items():
        mcp.tool(name=f"player_{_name}", description=_player_description(_spec))(
            _make_player_tool(_name)
        )

    # -- skills: the packaged features, exposed as MCP prompts --------------- #

    from .skills import list_skills

    for skill in list_skills():
        def _prompt(s=skill) -> str:
            return s.body

        mcp.prompt(name=skill.slug, description=skill.description)(_prompt)

    return mcp


def main() -> None:
    try:
        server = build_server()
    except RuntimeError as e:
        # A missing optional extra is an instruction, not a crash: show the
        # install hint on its own, never buried under a traceback.
        import sys

        print(e, file=sys.stderr)
        raise SystemExit(1)
    server.run()


if __name__ == "__main__":  # pragma: no cover
    main()
