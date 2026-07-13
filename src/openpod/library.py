"""The on-disk library: read/write the per-episode artifacts.

Layout (mirrors the Stage 1 spec)::

    .openpod/
      persona.md
      follows.yaml
      library/
        <show>/<episode>/
          meta.json          # SourceRef + bookkeeping
          transcript.json    # timed cues
          briefing.md        # cited, personalized (agent-authored)
          ideas.md           # key ideas, each with a deep-link
          clips/             # user-saved media + metadata
          notes.md           # the user's own annotations
      index/                 # local search index
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from .config import Workspace
from .models import Segment, SourceRef, Transcript, slugify


@dataclass
class LibraryEntry:
    """A single caught episode on disk."""

    workspace: Workspace
    show_slug: str
    episode_slug: str

    # -- identity ----------------------------------------------------------- #

    @property
    def entry_id(self) -> str:
        return f"{self.show_slug}/{self.episode_slug}"

    @property
    def dir(self) -> Path:
        return self.workspace.library_dir / self.show_slug / self.episode_slug

    # -- artifact paths ----------------------------------------------------- #

    @property
    def meta_path(self) -> Path:
        return self.dir / "meta.json"

    @property
    def transcript_path(self) -> Path:
        return self.dir / "transcript.json"

    @property
    def briefing_path(self) -> Path:
        return self.dir / "briefing.md"

    @property
    def ideas_path(self) -> Path:
        return self.dir / "ideas.md"

    @property
    def notes_path(self) -> Path:
        return self.dir / "notes.md"

    @property
    def summary_path(self) -> Path:
        return self.dir / "summary.md"

    @property
    def clips_dir(self) -> Path:
        return self.dir / "clips"

    # -- existence ---------------------------------------------------------- #

    def exists(self) -> bool:
        return self.meta_path.exists()

    # -- meta --------------------------------------------------------------- #

    def read_meta(self) -> dict:
        if not self.meta_path.exists():
            return {}
        return json.loads(self.meta_path.read_text(encoding="utf-8"))

    def write_meta(self, source: SourceRef, **extra) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        existing = self.read_meta()
        meta = {
            "entry_id": self.entry_id,
            "source": source.to_dict(),
            "caught_at": existing.get("caught_at") or _now(),
            "updated_at": _now(),
        }
        meta.update(extra)
        self.meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def source(self) -> Optional[SourceRef]:
        meta = self.read_meta()
        if "source" in meta:
            return SourceRef.from_dict(meta["source"])
        return None

    # -- transcript --------------------------------------------------------- #

    def read_transcript(self) -> Optional[Transcript]:
        if not self.transcript_path.exists():
            return None
        return Transcript.from_dict(
            json.loads(self.transcript_path.read_text(encoding="utf-8"))
        )

    def write_transcript(self, transcript: Transcript) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.transcript_path.write_text(
            json.dumps(transcript.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # -- segments ------------------------------------------------------------ #

    def read_segments(self) -> list[Segment]:
        """The episode's beat boundaries (persisted at catch time). Entries
        caught before segmentation existed get them recomputed on the fly —
        without writing, so reads stay reads."""
        meta = self.read_meta()
        if "segments" in meta:
            return [Segment.from_dict(s) for s in meta["segments"]]
        transcript = self.read_transcript()
        if transcript is None:
            return []
        from .segments import segment_transcript

        src = self.source()
        return segment_transcript(transcript,
                                  chapters=src.chapters if src else None)

    def read_chapters(self) -> list[Segment]:
        """The creator-chapter layer (verbatim), recovered from the source
        metadata. Empty when the source shipped no chapters."""
        src = self.source()
        if src is None or not src.chapters:
            return []
        from .segments import chapters_as_segments

        transcript = self.read_transcript()
        return chapters_as_segments(src.chapters,
                                    transcript.duration if transcript else None)

    # -- markdown artifacts ------------------------------------------------- #

    def write_briefing(self, markdown: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.briefing_path.write_text(markdown, encoding="utf-8")

    def write_ideas(self, markdown: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ideas_path.write_text(markdown, encoding="utf-8")

    # -- summary: the cross-session memory artifact --------------------------- #
    #
    # summary.md is the episode's durable distillation — written by an agent
    # (or the user) after actually engaging with the episode, and read back
    # by *future* sessions as context. The product's contract is deliberately
    # minimal: OpenPod owns WHERE it lives and the identity frontmatter
    # (entry_id / episode_key / timestamps, so it can sync and be recalled
    # from any device later); the BODY — structure, language, depth, what
    # counts as relevant — belongs entirely to whoever writes it.

    def read_summary(self, *, body_only: bool = False) -> Optional[str]:
        if not self.summary_path.exists():
            return None
        text = self.summary_path.read_text(encoding="utf-8")
        return _strip_frontmatter(text) if body_only else text

    def write_summary(self, markdown: str, *, append: bool = False) -> Path:
        """Write (or append a session's addition to) summary.md.

        ``append=True`` adds the text after the existing body — the caller
        decides whether a session adds a dated section or rewrites the whole
        distillation. Frontmatter is product-owned and regenerated on every
        write; any frontmatter inside ``markdown`` is stripped rather than
        duplicated."""
        self.dir.mkdir(parents=True, exist_ok=True)
        body = _strip_frontmatter(markdown).strip()
        existing = self.read_summary(body_only=True)
        if append and existing:
            body = existing.rstrip() + "\n\n" + body
        meta = self.read_meta()
        revisions = 1
        old = self.read_summary()
        if old is not None:
            m = re.search(r"^revisions:\s*(\d+)", old, re.M)
            revisions = (int(m.group(1)) if m else 0) + 1
        front = "\n".join(filter(None, [
            "---",
            f"entry_id: {self.entry_id}",
            f"episode_key: {meta['episode_key']}" if meta.get("episode_key") else None,
            f"updated_at: {_now()}",
            f"revisions: {revisions}",
            "---",
        ]))
        self.summary_path.write_text(front + "\n\n" + body + "\n",
                                     encoding="utf-8")
        return self.summary_path

    def append_note(self, note: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.notes_path.open("a", encoding="utf-8") as fh:
            fh.write(f"- {_now()} — {note}\n")

    def title(self) -> str:
        src = self.source()
        if src and src.title:
            return src.title
        return self.episode_slug.replace("-", " ")

    def show(self) -> str:
        src = self.source()
        if src and src.show:
            return src.show
        return self.show_slug.replace("-", " ")


class Library:
    """Collection-level operations over a workspace's caught episodes."""

    def __init__(self, workspace: Optional[Workspace] = None) -> None:
        self.workspace = workspace or Workspace()

    # -- entry lookup / creation ------------------------------------------- #

    def entry(self, show: str, episode: str) -> LibraryEntry:
        """Resolve (or reserve) an entry from human show/episode strings."""
        return LibraryEntry(self.workspace, slugify(show, fallback="show"),
                            slugify(episode, fallback="episode"))

    def entry_for(self, source: SourceRef) -> LibraryEntry:
        """Choose a stable directory for a source."""
        show = source.show or source.kind
        episode = source.title or source.guid or source.video_id \
            or source.episode_id or source.url or "episode"
        return self.entry(show, episode)

    def get(self, entry_id: str) -> Optional[LibraryEntry]:
        if "/" not in entry_id:
            return None
        show_slug, episode_slug = entry_id.split("/", 1)
        entry = LibraryEntry(self.workspace, show_slug, episode_slug)
        return entry if entry.exists() else None

    # -- iteration ---------------------------------------------------------- #

    def __iter__(self) -> Iterator[LibraryEntry]:
        base = self.workspace.library_dir
        if not base.is_dir():
            return
        for show_dir in sorted(p for p in base.iterdir() if p.is_dir()):
            for ep_dir in sorted(p for p in show_dir.iterdir() if p.is_dir()):
                entry = LibraryEntry(self.workspace, show_dir.name, ep_dir.name)
                if entry.exists():
                    yield entry

    def __len__(self) -> int:
        return sum(1 for _ in self)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


_frontmatter_re = re.compile(r"\A---\n.*?\n---\n?", re.S)


def _strip_frontmatter(text: str) -> str:
    return _frontmatter_re.sub("", text, count=1)
