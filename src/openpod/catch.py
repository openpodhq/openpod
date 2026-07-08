"""``catch`` — the core verb. Link -> durable artifacts in the local library.

Pipeline:
    resolve link  ->  transcript
    write meta.json + transcript.json
    extract key ideas + navigable TOC (deterministic, local)
    write ideas.md + briefing.md scaffold
    add to the local search index

No inference and no network beyond fetching the source itself. The agent
authors the personalized briefing afterwards by reading these artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import briefing as _briefing
from . import segments as _segments
from .config import Workspace
from .library import Library, LibraryEntry
from .models import Idea, Segment, SourceRef, Transcript


@dataclass
class CatchResult:
    entry: LibraryEntry
    source: SourceRef
    transcript: Transcript
    ideas: list[Idea]
    toc: list[Idea]
    segments: list[Segment]           # the beats layer (detected articulation)
    chapters: list[Segment]           # the creator layer ([] if none shipped)
    origin: Optional[SourceRef] = None   # what the user actually pasted
    identity: Optional[object] = None    # EpisodeIdentity, when resolved

    @property
    def entry_id(self) -> str:
        return self.entry.entry_id


def catch(link: str, *, workspace: Optional[Workspace] = None,
          kind: Optional[str] = None, transcript_path: Optional[str] = None,
          k_ideas: int = 8, index: bool = True,
          prefer_captions: bool = True, confirmed: bool = False) -> CatchResult:
    ws = (workspace or Workspace()).ensure()
    library = Library(ws)

    from .crosswalk import Crosswalk
    from .ingest.resolve import resolve_full

    cw = Crosswalk(ws)
    resolution = resolve_full(
        link, kind=kind, transcript_path=transcript_path,
        prefer_captions=prefer_captions, confirmed=confirmed, crosswalk=cw,
    )
    source, transcript = resolution.source, resolution.transcript
    if not len(transcript):
        raise ValueError(f"no transcript could be produced for: {link}")

    # Two anchor layers: the creator's chapters verbatim, and the detected
    # beats (topic detection, run inside over-long chapters). Deep-links
    # carry both, plus the exact moment — the reader picks the granularity.
    chapters = _segments.chapters_as_segments(source.chapters,
                                              transcript.duration) \
        if source.chapters else []
    segments = _segments.segment_transcript(transcript, chapters=source.chapters)

    identity = _record_identity(cw, resolution)

    entry = library.entry_for(source)
    extra = {"segments": [s.to_dict() for s in segments]}
    if resolution.origin is not None:
        extra["origin"] = resolution.origin.to_dict()
    if identity is not None:
        extra["episode_key"] = identity.episode_key
    entry.write_meta(source, **extra)
    entry.write_transcript(transcript)

    ideas = _briefing.extract_ideas(transcript, source, k=k_ideas)
    _segments.annotate(ideas, segments, source, chapters=chapters)
    toc = _briefing.build_toc(transcript, source, structure=segments)
    persona = _read_persona(ws)

    entry.write_ideas(_briefing.ideas_markdown(ideas, source))
    entry.write_briefing(_briefing.briefing_scaffold(source, transcript, toc, persona=persona))

    if index:
        from .search.index import SearchIndex

        SearchIndex(ws).add_entry(entry)

    return CatchResult(entry=entry, source=source, transcript=transcript,
                      ideas=ideas, toc=toc, segments=segments,
                      chapters=chapters, origin=resolution.origin,
                      identity=identity)


def _record_identity(cw, resolution):
    """Write everything this resolution learned into the crosswalk.

    Identity is a fact about the episode; recording it here means the same
    episode — pasted by anyone, on any platform — never gets re-resolved.
    """
    from .identity import source_episode_key
    from .models import EpisodeIdentity

    source, origin = resolution.source, resolution.origin
    ident = resolution.identity or EpisodeIdentity()

    key_basis = ident.feed_url or (source.url if source.kind == "podcast" else None)
    if key_basis:
        from .identity import episode_key as _ekey
        ident.episode_key, ident.key_confidence = _ekey(
            key_basis, guid=ident.rss_guid or source.guid,
            audio_url=ident.enclosure_url or source.audio_url,
            title=ident.title or source.title,
            published=ident.published or source.published)
    else:
        ident.episode_key, ident.key_confidence = source_episode_key(source)

    # Fold in ids visible on the source / origin refs.
    if source.kind == "youtube" and source.video_id:
        ident.youtube_video_id = ident.youtube_video_id or source.video_id
        ident.methods.setdefault("youtube_video_id", "url")
    if origin is not None:
        if origin.kind == "spotify" and origin.episode_id:
            ident.spotify_episode_id = ident.spotify_episode_id or origin.episode_id
            ident.methods.setdefault("spotify_episode_id", "url")
        if origin.kind == "youtube" and origin.video_id:
            ident.youtube_video_id = ident.youtube_video_id or origin.video_id
            ident.methods.setdefault("youtube_video_id", "url")
    ident.show = ident.show or source.show
    ident.title = ident.title or source.title
    ident.published = ident.published or source.published
    ident.duration = ident.duration or source.duration
    ident.enclosure_url = ident.enclosure_url or source.audio_url
    ident.rss_guid = ident.rss_guid or source.guid

    if not ident.episode_key:
        return ident if resolution.identity is not None else None
    cw.put(ident)
    if ident.feed_url:
        cw.put_show(ident.feed_url, show=ident.show,
                    apple_show_id=ident.apple_show_id,
                    spotify_show_id=ident.spotify_show_id,
                    apple_country=ident.apple_country)
    return ident


def _read_persona(workspace: Workspace) -> Optional[str]:
    p = workspace.persona_file
    return p.read_text(encoding="utf-8") if p.exists() else None
