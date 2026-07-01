"""Core data types shared across OpenPod.

These are plain dataclasses with explicit ``to_dict`` / ``from_dict`` so the
on-disk artifacts (``transcript.json`` etc.) have a stable, human-readable
schema. No third-party dependencies here — this module is safe to import
anywhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Optional


# --------------------------------------------------------------------------- #
# Source references
# --------------------------------------------------------------------------- #

# Recognised source kinds. "file" is a local media/transcript path (offline).
SOURCE_KINDS = ("youtube", "podcast", "spotify", "file")


@dataclass
class SourceRef:
    """Where an episode came from, plus everything needed to build a deep-link.

    Only ``kind`` is required. The rest are filled in as they're discovered
    during ingestion.
    """

    kind: str
    url: Optional[str] = None
    # Provider-specific identifiers used for deep-links.
    video_id: Optional[str] = None          # YouTube
    episode_id: Optional[str] = None         # Spotify episode id
    guid: Optional[str] = None               # podcast RSS <guid>
    # Human metadata.
    show: Optional[str] = None
    title: Optional[str] = None
    published: Optional[str] = None           # ISO date string
    # Playback / extraction.
    audio_url: Optional[str] = None           # open enclosure URL (podcasts)
    duration: Optional[float] = None          # seconds, if known

    def __post_init__(self) -> None:
        if self.kind not in SOURCE_KINDS:
            raise ValueError(
                f"unknown source kind {self.kind!r}; expected one of {SOURCE_KINDS}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SourceRef":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


# --------------------------------------------------------------------------- #
# Transcripts
# --------------------------------------------------------------------------- #


@dataclass
class Cue:
    """A single timed span of transcript text.

    ``start``/``end`` are in seconds. ``end`` may be ``None`` for point cues.
    """

    start: float
    text: str
    end: Optional[float] = None
    speaker: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"start": round(self.start, 3), "text": self.text}
        if self.end is not None:
            d["end"] = round(self.end, 3)
        if self.speaker is not None:
            d["speaker"] = self.speaker
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Cue":
        return cls(
            start=float(d["start"]),
            text=str(d.get("text", "")),
            end=float(d["end"]) if d.get("end") is not None else None,
            speaker=d.get("speaker"),
        )


@dataclass
class Transcript:
    """An ordered list of cues plus provenance."""

    cues: list[Cue] = field(default_factory=list)
    source: str = "unknown"        # e.g. "youtube-captions", "podcast:transcript", "asr:whisper"
    language: Optional[str] = None
    word_level: bool = False        # True if timing is word-accurate (clip-safe)

    # -- basic access ------------------------------------------------------- #

    def __len__(self) -> int:
        return len(self.cues)

    def __iter__(self) -> Iterable[Cue]:
        return iter(self.cues)

    @property
    def duration(self) -> float:
        if not self.cues:
            return 0.0
        last = self.cues[-1]
        return last.end if last.end is not None else last.start

    def text(self, sep: str = " ") -> str:
        """Flatten to a single string."""
        return sep.join(c.text.strip() for c in self.cues if c.text.strip())

    def window(self, start: float, end: float) -> list[Cue]:
        """Return cues that overlap the [start, end] time window."""
        out = []
        for c in self.cues:
            c_end = c.end if c.end is not None else c.start
            if c_end >= start and c.start <= end:
                out.append(c)
        return out

    def cue_at(self, seconds: float) -> Optional[Cue]:
        """The cue active at a given time (nearest preceding cue)."""
        chosen = None
        for c in self.cues:
            if c.start <= seconds:
                chosen = c
            else:
                break
        return chosen

    # -- serialization ------------------------------------------------------ #

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "language": self.language,
            "word_level": self.word_level,
            "cues": [c.to_dict() for c in self.cues],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Transcript":
        return cls(
            cues=[Cue.from_dict(c) for c in d.get("cues", [])],
            source=d.get("source", "unknown"),
            language=d.get("language"),
            word_level=bool(d.get("word_level", False)),
        )


# --------------------------------------------------------------------------- #
# Derived artifacts
# --------------------------------------------------------------------------- #


@dataclass
class Idea:
    """A salient extracted moment, each carrying a deep-link back to the source."""

    text: str
    start: float
    end: Optional[float] = None
    deeplink: Optional[str] = None
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class SearchHit:
    """One result from a local library search."""

    show: str
    episode: str
    text: str
    start: float
    score: float = 0.0
    deeplink: Optional[str] = None
    entry_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


# --------------------------------------------------------------------------- #
# Small shared helpers
# --------------------------------------------------------------------------- #

_slug_re = re.compile(r"[^a-z0-9]+")


def slugify(value: str, *, max_len: int = 80, fallback: str = "item") -> str:
    """Filesystem-safe slug: lowercase, hyphen-separated, ascii-ish."""
    value = (value or "").strip().lower()
    slug = _slug_re.sub("-", value).strip("-")
    slug = slug[:max_len].strip("-")
    return slug or fallback


def format_timestamp(seconds: float) -> str:
    """Seconds -> H:MM:SS or M:SS for display."""
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
