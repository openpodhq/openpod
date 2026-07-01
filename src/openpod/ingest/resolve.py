"""Detect what a link is and produce a :class:`SourceRef`.

Also the single entry point that drives ingestion end-to-end:
``resolve(link)`` -> ``(SourceRef, Transcript)``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from ..models import SourceRef, Transcript

_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
_SPOTIFY_HOSTS = {"open.spotify.com", "spotify.com"}


def youtube_video_id(url: str) -> Optional[str]:
    u = urlparse(url)
    host = u.netloc.lower()
    if host == "youtu.be":
        return u.path.lstrip("/").split("/")[0] or None
    if host in _YT_HOSTS:
        if u.path == "/watch":
            v = parse_qs(u.query).get("v")
            return v[0] if v else None
        m = re.match(r"/(embed|shorts|live|v)/([^/?#]+)", u.path)
        if m:
            return m.group(2)
    return None


def spotify_episode_id(url: str) -> Optional[str]:
    u = urlparse(url)
    if u.netloc.lower() in _SPOTIFY_HOSTS:
        m = re.match(r"/episode/([^/?#]+)", u.path)
        if m:
            return m.group(1)
    return None


def detect_kind(link: str) -> str:
    """Classify a link/path into a source kind (best effort)."""
    # Local file?
    if not re.match(r"^[a-z]+://", link, re.I):
        if Path(link).expanduser().exists():
            return "file"

    u = urlparse(link)
    host = u.netloc.lower()
    if host in _YT_HOSTS:
        return "youtube"
    if host in _SPOTIFY_HOSTS:
        return "spotify"
    # RSS/podcast: an .xml feed, or something we can only treat as a podcast feed.
    if u.path.lower().endswith((".xml", ".rss")) or "rss" in u.path.lower() or "feed" in host:
        return "podcast"
    # Direct audio enclosure.
    if u.path.lower().endswith((".mp3", ".m4a", ".aac", ".ogg", ".wav")):
        return "podcast"
    # Default: treat http(s) links we can't place as a podcast/feed candidate.
    return "podcast"


def resolve(link: str, *, kind: str | None = None,
            transcript_path: str | None = None,
            prefer_captions: bool = True) -> tuple[SourceRef, Transcript]:
    """Resolve a link to (SourceRef, Transcript).

    ``transcript_path`` lets callers supply a local transcript file directly
    (handy offline and for ``--transcript`` on the CLI), bypassing the network.
    """
    kind = kind or detect_kind(link)

    # An explicitly supplied transcript short-circuits all fetching.
    if transcript_path:
        from .. import transcript as tx
        source = _bare_source(link, kind)
        t = tx.load_file(transcript_path)
        return source, t

    if kind == "youtube":
        from .youtube import ingest_youtube
        return ingest_youtube(link, prefer_captions=prefer_captions)

    if kind == "podcast":
        from .rss import ingest_podcast
        return ingest_podcast(link)

    if kind == "file":
        return _ingest_file(link)

    if kind == "spotify":
        # No open transcript source for Spotify; caller must supply audio/transcript.
        raise NotImplementedError(
            "Spotify episodes expose no open transcript. Provide --transcript "
            "with a local caption file, or catch the episode from its RSS/YouTube source."
        )

    raise ValueError(f"cannot resolve link of kind {kind!r}: {link}")


def _bare_source(link: str, kind: str) -> SourceRef:
    src = SourceRef(kind=kind, url=link if kind != "file" else None)
    if kind == "youtube":
        src.video_id = youtube_video_id(link)
    elif kind == "spotify":
        src.episode_id = spotify_episode_id(link)
    return src


def _ingest_file(link: str) -> tuple[SourceRef, Transcript]:
    from .. import transcript as tx

    p = Path(link).expanduser()
    src = SourceRef(kind="file", title=p.stem, audio_url=None)
    # If it's a transcript, parse it; otherwise it's media needing ASR.
    if p.suffix.lower() in (".vtt", ".srt", ".json3", ".json"):
        return src, tx.load_file(p)
    from ..asr import transcribe
    return src, transcribe(str(p))
