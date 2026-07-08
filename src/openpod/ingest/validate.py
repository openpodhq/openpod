"""Content-type validation — a defined support matrix, not incidental luck.

Before any URL is piped into transcription, sniff what it actually is:
HEAD-request MIME first, magic bytes when the server lies or stays generic.
Tracking redirectors (``chrt.fm`` and friends) are followed to the terminal
URL, which is what gets cached and validated. Anything outside the support
matrix raises :class:`~openpod.errors.UnsupportedFormatError` with the
observed type — never a silent best-effort fallback into whisper.
"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from typing import Optional

from ..errors import UnsupportedFormatError

_UA = {"User-Agent": "OpenPod/0.1 (+https://github.com/openpod/openpod)"}

# The deliberate support matrix.
AUDIO_MIME = {
    "audio/mpeg", "audio/mp3", "audio/mp4", "audio/m4a", "audio/x-m4a",
    "audio/aac", "audio/aacp", "audio/ogg", "audio/opus", "audio/vorbis",
    "audio/wav", "audio/x-wav", "audio/wave", "audio/flac", "audio/x-flac",
    "audio/webm",
}
VIDEO_MIME = {
    "video/mp4", "video/webm", "video/quicktime", "video/x-matroska",
}
FEED_MIME = {
    "application/rss+xml", "application/atom+xml", "application/xml",
    "text/xml",
}

# Magic-byte signatures for when Content-Type is missing or generic.
_MAGIC = (
    (b"ID3", "audio/mpeg"),
    (b"\xff\xfb", "audio/mpeg"), (b"\xff\xf3", "audio/mpeg"),
    (b"\xff\xf2", "audio/mpeg"), (b"\xff\xf1", "audio/aac"),
    (b"OggS", "audio/ogg"),
    (b"fLaC", "audio/flac"),
    (b"RIFF", "audio/wav"),
    (b"\x1aE\xdf\xa3", "video/webm"),
    (b"<?xml", "application/xml"), (b"<rss", "application/rss+xml"),
    (b"<!DOCTYPE html", "text/html"), (b"<html", "text/html"),
)


@dataclass
class MediaInfo:
    url: str                    # terminal URL after redirects — cache this
    content_type: Optional[str]
    kind: str                   # "audio" | "video" | "feed" | "html" | "unknown"

    @property
    def supported(self) -> bool:
        return self.kind in ("audio", "video", "feed")


def classify_mime(mime: Optional[str]) -> str:
    m = (mime or "").split(";")[0].strip().lower()
    if m in AUDIO_MIME:
        return "audio"
    if m in VIDEO_MIME:
        return "video"
    if m in FEED_MIME:
        return "feed"
    if m == "text/html":
        return "html"
    if m.startswith("audio/"):
        return "audio"
    if m.startswith("video/"):
        return "video"
    return "unknown"


def classify_bytes(head: bytes) -> Optional[str]:
    stripped = head.lstrip()
    for sig, mime in _MAGIC:
        if head.startswith(sig) or stripped.startswith(sig):
            return mime
    # MP4-family containers: 'ftyp' at offset 4.
    if len(head) > 8 and head[4:8] == b"ftyp":
        return "audio/mp4" if head[8:11] in (b"M4A", b"M4B") else "video/mp4"
    return None


def sniff(url: str, *, timeout: int = 20) -> MediaInfo:
    """Determine what a URL serves: terminal URL + MIME + coarse kind.

    HEAD first (follows redirects, resolving tracking-redirector chains);
    when the type comes back missing or generic, GET the first KB and check
    magic bytes.
    """
    final_url, mime = url, None
    try:
        req = urllib.request.Request(url, headers=_UA, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            final_url = resp.geturl()
            mime = resp.headers.get("Content-Type")
    except Exception:
        pass  # some hosts reject HEAD; fall through to the ranged GET

    kind = classify_mime(mime)
    if kind == "unknown":
        try:
            req = urllib.request.Request(
                url, headers={**_UA, "Range": "bytes=0-1023"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                final_url = resp.geturl()
                mime = classify_bytes(resp.read(1024)) or mime
                kind = classify_mime(mime)
        except Exception:
            pass

    return MediaInfo(url=final_url, content_type=mime, kind=kind)


def ensure_media(url: str, *, allow_video: bool = True) -> MediaInfo:
    """Validate that ``url`` serves audio (or video when allowed) before it
    is downloaded and transcribed. Raises with the observed type otherwise."""
    info = sniff(url)
    if info.kind == "audio" or (allow_video and info.kind == "video"):
        return info
    if info.kind == "video":
        raise UnsupportedFormatError(
            url, content_type=info.content_type,
            detail="this is a video stream and video was not requested")
    raise UnsupportedFormatError(
        url, content_type=info.content_type,
        detail="expected an audio enclosure; got "
               f"{info.kind}. If this is a tracking redirector, the terminal "
               f"URL was {info.url}")
