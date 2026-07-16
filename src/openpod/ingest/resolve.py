"""Detect what a link is and produce a :class:`SourceRef`.

Also the single entry point that drives ingestion end-to-end:
``resolve(link)`` -> ``(SourceRef, Transcript)`` and its richer sibling
``resolve_full(link)`` -> :class:`Resolution` (adds identity + match
provenance for the crosswalk and the confirmation gate).

Two rules enforced here:

* **No silent defaults.** A link we can't classify is ``"unknown"`` and
  raises a structured :class:`~openpod.errors.UnresolvedLinkError` — it is
  never quietly shoved into the RSS path.
* **No unconfirmed fuzzy matches.** A platform match below the confidence
  threshold raises :class:`~openpod.errors.NeedsConfirmation` carrying the
  candidate, unless the caller passes ``confirmed=True`` (i.e. the user has
  vetted it).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from dataclasses import dataclass, field

from ..crosswalk import CONFIRM_THRESHOLD
from ..errors import NeedsConfirmation, UnresolvedLinkError
from ..models import EpisodeIdentity, SourceRef, Transcript

_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
_SPOTIFY_HOSTS = {"open.spotify.com", "spotify.com"}
_APPLE_HOSTS = {"podcasts.apple.com", "itunes.apple.com"}

_AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".ogg", ".wav")


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
    """Classify a link/path into a source kind.

    Unrecognized http(s) links are ``"unknown"`` — classification is a
    contract, not a guess, so nothing falls through into the RSS parser
    silently. Callers can still force a kind with ``kind=``.
    """
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
    if host in _APPLE_HOSTS:
        return "apple"
    # RSS/podcast: an .xml feed, or an explicit feed-ish URL.
    if u.path.lower().endswith((".xml", ".rss")) or "rss" in u.path.lower() or "feed" in host:
        return "podcast"
    # Direct audio enclosure.
    if u.path.lower().endswith(_AUDIO_EXTS):
        return "podcast"
    return "unknown"


def origin_ref(link: str, kind: Optional[str] = None) -> SourceRef:
    """A SourceRef for *what the user pasted* — kept separate from the
    transcript source so output links can always render on the user's
    platform (origin/source decoupling)."""
    kind = kind or detect_kind(link)
    if kind == "file":
        return _file_ref(link)
    src = SourceRef(kind=kind, url=link)
    if kind == "youtube":
        src.video_id = youtube_video_id(link)
    elif kind == "spotify":
        src.episode_id = spotify_episode_id(link)
    return src


_TRANSCRIPT_EXTS = (".vtt", ".srt", ".json3", ".json")


def _file_ref(link: str) -> SourceRef:
    """A SourceRef for a local file, carrying the resolved path.

    The path is the file's only identity: without it, every local catch slugs
    to the same library directory and silently replaces the previous one, and
    ``clip`` can't find media that is sitting right where the user said it is.
    A media path goes in ``audio_url`` so :func:`openpod.media.get_media`
    can cut from it in place (never copied, never uploaded)."""
    p = Path(link).expanduser()
    try:
        resolved = str(p.resolve())
    except OSError:
        resolved = str(p)
    src = SourceRef(kind="file", title=p.stem or None, url=resolved)
    if p.suffix.lower() not in _TRANSCRIPT_EXTS:
        src.audio_url = resolved
    return src


@dataclass
class Resolution:
    """Everything one resolution learned, for callers that persist it."""

    source: SourceRef
    transcript: Transcript
    origin: SourceRef
    identity: Optional[EpisodeIdentity] = None
    confidence: float = 1.0
    method: str = "direct"
    tried: list = field(default_factory=list)


def resolve(link: str, *, kind: str | None = None,
            transcript_path: str | None = None,
            prefer_captions: bool = True,
            confirmed: bool = False) -> tuple[SourceRef, Transcript]:
    """Resolve a link to (SourceRef, Transcript). See :func:`resolve_full`."""
    r = resolve_full(link, kind=kind, transcript_path=transcript_path,
                     prefer_captions=prefer_captions, confirmed=confirmed)
    return r.source, r.transcript


def resolve_full(link: str, *, kind: str | None = None,
                 transcript_path: str | None = None,
                 prefer_captions: bool = True,
                 confirmed: bool = False,
                 crosswalk=None,
                 asr: str = "auto",
                 asr_auto_threshold: float = 180.0,
                 caption_languages: list | None = None,
                 progress=None) -> Resolution:
    """Resolve a link end-to-end, returning identity + match provenance.

    ``crosswalk`` (a :class:`openpod.crosswalk.Crosswalk`) enables the cache
    shortcut: an episode already identified — by any user of this workspace,
    for any reason — is never re-identified over the network.
    """
    kind = kind or detect_kind(link)
    origin = origin_ref(link, kind)

    # An explicitly supplied transcript short-circuits all fetching.
    if transcript_path:
        from .. import transcript as tx
        t = tx.load_file(transcript_path)
        return Resolution(source=origin, transcript=t, origin=origin,
                          method="transcript-file")

    if kind == "youtube":
        from .youtube import ingest_youtube
        source, t = ingest_youtube(link, prefer_captions=prefer_captions,
                                   languages=caption_languages, asr=asr,
                                   auto_threshold=asr_auto_threshold,
                                   progress=progress)
        return Resolution(source=source, transcript=t, origin=origin)

    if kind == "podcast":
        from .rss import ingest_podcast
        source, t = ingest_podcast(link, progress=progress)
        return Resolution(source=source, transcript=t, origin=origin)

    if kind == "file":
        source, t = _ingest_file(link)
        return Resolution(source=source, transcript=t, origin=origin)

    if kind in ("apple", "spotify"):
        return _resolve_platform(link, kind, origin, confirmed=confirmed,
                                 crosswalk=crosswalk)

    raise UnresolvedLinkError(link, kind=kind, tried=["detect_kind"])


def _resolve_platform(link: str, kind: str, origin: SourceRef, *,
                      confirmed: bool, crosswalk=None) -> Resolution:
    """Apple/Spotify: identity-first resolution with the confirmation gate."""
    ident = _cached_identity(link, kind, crosswalk)
    if ident is not None and ident.feed_url and ident.rss_guid:
        # Cache hit: identity is settled, go straight to the feed.
        from .rss import load_feed, ingest_podcast
        feed = load_feed(ident.feed_url)
        item = next((i for i in feed.items
                     if i.guid and i.guid.strip() == ident.rss_guid.strip()),
                    None)
        if item is not None:
            source, t = ingest_podcast(ident.feed_url, item=item,
                                       feed_title=feed.title or ident.show)
            return Resolution(source=source, transcript=t, origin=origin,
                              identity=ident, confidence=ident.match_confidence,
                              method="crosswalk-cache")

    if kind == "apple":
        from .apple import prepare_apple
        ident, feed, item, confidence, method = prepare_apple(link)
    else:
        from .spotify import prepare_spotify
        ident, feed, item, confidence, method = prepare_spotify(link)

    if confidence < CONFIRM_THRESHOLD and not confirmed:
        raise NeedsConfirmation(
            candidate={
                "title": item.title, "show": feed.title or ident.show,
                "published": item.published,
                "duration": ident.duration,
                "feed_url": ident.feed_url, "guid": item.guid,
            },
            confidence=confidence, method=method, link=link)
    if confirmed:
        ident.note("feed_item", "user-confirmed", 1.0)
        ident.match_confidence = 1.0

    from .rss import ingest_podcast
    source, t = ingest_podcast(ident.feed_url, item=item,
                               feed_title=feed.title or ident.show)
    ident.duration = ident.duration or source.duration
    return Resolution(source=source, transcript=t, origin=origin,
                      identity=ident, confidence=confidence, method=method)


def _cached_identity(link: str, kind: str, crosswalk) -> Optional[EpisodeIdentity]:
    if crosswalk is None:
        return None
    if kind == "spotify":
        eid = spotify_episode_id(link)
        return crosswalk.find_by("spotify_episode_id", eid) if eid else None
    if kind == "apple":
        from .apple import parse_apple_url
        ref = parse_apple_url(link)
        if ref and ref.episode_id:
            return crosswalk.find_by("apple_episode_id", ref.episode_id)
    return None


def _bare_source(link: str, kind: str) -> SourceRef:
    return origin_ref(link, kind)


def _ingest_file(link: str) -> tuple[SourceRef, Transcript]:
    from .. import transcript as tx

    p = Path(link).expanduser()
    src = _file_ref(link)
    # If it's a transcript, parse it; otherwise it's media needing ASR.
    if p.suffix.lower() in _TRANSCRIPT_EXTS:
        return src, tx.load_file(p)
    from ..asr import transcribe
    return src, transcribe(str(p))
