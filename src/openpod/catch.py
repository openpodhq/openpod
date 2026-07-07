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
from .ingest import resolve
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

    @property
    def entry_id(self) -> str:
        return self.entry.entry_id


def catch(link: str, *, workspace: Optional[Workspace] = None,
          kind: Optional[str] = None, transcript_path: Optional[str] = None,
          k_ideas: int = 8, index: bool = True,
          prefer_captions: bool = True) -> CatchResult:
    ws = (workspace or Workspace()).ensure()
    library = Library(ws)

    source, transcript = resolve(
        link, kind=kind, transcript_path=transcript_path,
        prefer_captions=prefer_captions,
    )
    if not len(transcript):
        raise ValueError(f"no transcript could be produced for: {link}")

    # Two anchor layers: the creator's chapters verbatim, and the detected
    # beats (topic detection, run inside over-long chapters). Deep-links
    # carry both, plus the exact moment — the reader picks the granularity.
    chapters = _segments.chapters_as_segments(source.chapters,
                                              transcript.duration) \
        if source.chapters else []
    segments = _segments.segment_transcript(transcript, chapters=source.chapters)

    entry = library.entry_for(source)
    entry.write_meta(source, segments=[s.to_dict() for s in segments])
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
                      chapters=chapters)


def _read_persona(workspace: Workspace) -> Optional[str]:
    p = workspace.persona_file
    return p.read_text(encoding="utf-8") if p.exists() else None
