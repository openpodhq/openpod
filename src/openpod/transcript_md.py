"""``transcript.md`` — the rendered, readable, deep-linked view of a
transcript, written into every library entry next to ``transcript.json``.

``transcript.json`` answers directed questions through the agent;
``transcript.md`` exists for the other half of the job — skimming, for when
the user does not yet know what to ask. Same cues, reflowed into paragraphs
(``reflow.py``), chaptered as ``##`` headings so the host editor's outline
pane becomes a chapter rail, one blue ``▸ m:ss`` badge per paragraph deep-
linking the player at that moment — and nothing else in the file is blue.

Normative spec: ``Docs/product/OpenPod_Transcript_Markdown_Spec.md``.
Machine-authored and regenerable: byte-identical for identical input
(TM-15), overwritten wholesale on re-catch (TM-16), rebuilt without
re-transcribing by ``openpod render`` (TM-17).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from . import theme
from .config import Workspace
from .models import Segment, SourceRef, Transcript, format_timestamp
from .reflow import assign_chapters, paragraphs

# The hosted player, per the share.ts momentUrl() contract (TM-9). A
# constant, like card.py's BACKLINK: nothing in .openpod/ is published, and
# the links are text — writing them contacts nothing.
PLAYER_ORIGIN = "https://player.openpod.dev"


def moment_url(feed: Optional[str], seconds: float, *,
               guid: Optional[str] = None,
               episode_key: Optional[str] = None) -> Optional[str]:
    """The player moment URL — ``{origin}/#listen?feed=…&t=…&guid=…``,
    falling back to ``&ek=<episode_key>`` when the entry has no GUID (TM-9).
    Seconds floor to integers per that contract (TM-10). No feed, or neither
    guid nor episode_key → ``None``: a dead link is worse than no link
    (TM-11)."""
    if not feed or not (guid or episode_key):
        return None
    params = {"feed": feed, "t": str(int(seconds))}
    if guid:
        params["guid"] = guid
    else:
        params["ek"] = episode_key
    return f"{PLAYER_ORIGIN}/#listen?" + urlencode(params)


def _yaml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_transcript_md(transcript: Transcript, *,
                         source: Optional[SourceRef],
                         chapters: list[Segment],
                         episode_key: Optional[str],
                         show: str, title: str) -> str:
    """Render the document. Pure string work over in-memory objects — no
    network, no LLM, nothing non-deterministic."""
    ch_starts = [c.start for c in chapters]
    paras = paragraphs(transcript.cues, ch_starts)
    if chapters:
        assign_chapters(paras, ch_starts)

    feed = source.url if source is not None and source.kind == "podcast" else None
    guid = source.guid if source is not None else None
    published = source.published if source is not None else None

    def url_at(seconds: float) -> Optional[str]:
        return moment_url(feed, seconds, guid=guid, episode_key=episode_key)

    # Speaker display names: structured source speakers first (never agent
    # memory) — a cue's short name maps to the full one by first word.
    full_names = [s.get("name") for s in (source.speakers if source else None) or []
                  if isinstance(s, dict) and s.get("name")]
    name_map = {n.split()[0]: n for n in full_names}
    cue_speakers: list[str] = []
    for c in transcript.cues:
        if c.speaker and c.speaker not in cue_speakers:
            cue_speakers.append(c.speaker)
    front_speakers = full_names or [name_map.get(s, s) for s in cue_speakers]

    L: list[str] = []
    refs: list[str] = []

    # -- frontmatter (summary.md convention; no timestamps — TM-15) -------- #
    L += ["---", f"show: {_yaml_str(show)}", f"title: {_yaml_str(title)}"]
    if published:
        L.append(f"published: {published}")
    L.append(f'duration: "{format_timestamp(transcript.duration)}"')
    L.append(f"source: {transcript.source}")
    if cue_speakers:  # omitted entirely when no cue carries a speaker (§6)
        L.append(f"speakers: [{', '.join(front_speakers)}]")
    if episode_key:
        L.append(f"episode_key: {episode_key}")
    L += ["---", ""]

    # -- header ------------------------------------------------------------ #
    meta_rule = " · ".join(filter(None, [
        show, published, format_timestamp(transcript.duration),
        f"transcript by `{transcript.source}`",
    ]))
    L += [f"# {title}", "", meta_rule, ""]
    top = url_at(0)
    if top:
        L += [f"[{theme.GLYPH} Play from the top]({top})", ""]

    def badge(seconds: float, ref: str) -> str:
        """Reference-style moment badge; degrades to inline code when the
        entry can't link (TM-11) — the glyph survives, the promise is not
        made."""
        url = url_at(seconds)
        if url is None:
            return f"`{theme.moment_plain(seconds)}`"
        refs.append(f"[{ref}]: {url}")
        return f"[{theme.moment_plain(seconds)}][{ref}]"

    if chapters:
        L += ["## Contents", ""]
        for i, ch in enumerate(chapters):
            L.append(f"- {badge(ch.start, f'c{i}')} — {ch.title}")
        L.append("")

    # -- body -------------------------------------------------------------- #
    current_chapter = -1
    last_speaker: Optional[str] = None
    for p in paras:
        if chapters and p.chapter != current_chapter:
            current_chapter = p.chapter
            L += ["", f"## {chapters[current_chapter].title}", ""]
            last_speaker = None
        head = badge(p.start, f"t{int(p.start)}")
        who = ""
        if p.speaker and p.speaker != last_speaker:
            who = f"**{name_map.get(p.speaker, p.speaker)}:** "
            last_speaker = p.speaker
        L += [f"{head} — {who}{p.text}", ""]

    # -- pooled reference definitions (TM-8) -------------------------------- #
    if refs:
        seen: set[str] = set()
        deduped = [r for r in refs if not (r in seen or seen.add(r))]
        L += ["", "<!-- moment links -->", ""] + deduped
    L.append("")
    return "\n".join(L)


def render_entry(entry_id: str, *,
                 workspace: Optional[Workspace] = None) -> Path:
    """Re-render ``transcript.md`` from the existing ``transcript.json`` —
    no re-fetch, no ASR (TM-17). Never written without its source."""
    from .library import Library

    ws = workspace or Workspace()
    entry = Library(ws).get(entry_id)
    if entry is None:
        raise ValueError(f"no caught episode with id {entry_id!r}")
    transcript = entry.read_transcript()
    if transcript is None:
        raise ValueError(
            "no transcript.json for this entry — transcript.md is only ever "
            "derived from it; catch the episode first")
    meta = entry.read_meta()
    md = render_transcript_md(
        transcript,
        source=entry.source(),
        chapters=entry.read_chapters(),
        episode_key=meta.get("episode_key"),
        show=entry.show(), title=entry.title(),
    )
    return entry.write_transcript_md(md)
