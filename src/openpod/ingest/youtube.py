"""YouTube ingestion — captions first, ASR only as a fallback.

Caption cue timing (±2–4s, drifts early) is good enough for navigation, and it
costs nothing and needs no Whisper. We only download audio for ASR when no
caption track exists.

This module keeps its heavy dependencies (``youtube-transcript-api``, ``yt-dlp``)
lazy so the base package installs without them; install the ``youtube`` extra to
enable it.
"""

from __future__ import annotations

from typing import Optional

from ..models import SourceRef, Transcript
from .resolve import youtube_video_id


def ingest_youtube(url: str, *, prefer_captions: bool = True,
                   languages: Optional[list[str]] = None
                   ) -> tuple[SourceRef, Transcript]:
    video_id = youtube_video_id(url)
    if not video_id:
        raise ValueError(f"could not extract a YouTube video id from: {url}")

    source = SourceRef(kind="youtube", url=url, video_id=video_id)
    _enrich_metadata(source)

    if prefer_captions:
        t = _captions(video_id, languages or ["en"])
        if t is not None and len(t):
            return source, t

    # Fallback: pull audio with yt-dlp and transcribe locally.
    from ..asr import transcribe, download_audio

    audio = download_audio(url)
    return source, transcribe(audio)


def _captions(video_id: str, languages: list[str]) -> Optional[Transcript]:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
    except ImportError:
        return None
    try:
        # API surface differs across versions; support both the classic
        # classmethod and the newer instance API.
        fetched = None
        if hasattr(YouTubeTranscriptApi, "get_transcript"):
            fetched = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
        else:  # pragma: no cover - version dependent
            api = YouTubeTranscriptApi()
            data = api.fetch(video_id, languages=languages)
            fetched = data.to_raw_data() if hasattr(data, "to_raw_data") else list(data)
    except Exception:
        return None

    from .. import transcript as tx

    cues = tx.from_cue_dicts(fetched)
    return Transcript(cues=cues, source="youtube-captions", language=languages[0])


def _enrich_metadata(source: SourceRef) -> None:
    """Best-effort title/show/duration via yt-dlp (metadata only, no download)."""
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        return
    try:
        opts = {"quiet": True, "skip_download": True, "no_warnings": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(source.url, download=False)
        source.title = info.get("title") or source.title
        source.show = info.get("uploader") or info.get("channel") or source.show
        source.duration = info.get("duration") or source.duration
    except Exception:
        return
