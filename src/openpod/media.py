"""Shared media cache — one download per episode, whoever asks.

Clip, ASR, alignment, and external tools all need the same source media;
before this cache each one re-downloaded it. Media now lands once under
``.openpod/media/<entry-slug>/`` and every consumer reuses it.

Video is preserved when the source has it and the caller wants it — the
clip pipeline stopped silently degrading YouTube video to audio-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import Workspace
from .models import SourceRef

_VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov")


@dataclass
class Media:
    path: Path
    has_video: bool
    cached: bool          # True when served from cache, no download happened


def source_has_video(source: Optional[SourceRef]) -> bool:
    """Whether the source *can* provide a video track."""
    return source is not None and source.kind == "youtube"


def _cache_dir(workspace: Workspace, entry_id: str) -> Path:
    return workspace.media_dir / entry_id.replace("/", "--")


def _find_cached(cache: Path, *, want_video: bool) -> Optional[Path]:
    if not cache.is_dir():
        return None
    files = [p for p in cache.iterdir() if p.is_file() and p.stem.startswith("media")]
    video = [p for p in files if p.suffix.lower() in _VIDEO_EXTS]
    audio = [p for p in files if p not in video]
    if want_video:
        return video[0] if video else None
    # Audio wanted: an audio file is ideal; a video file still carries audio.
    return (audio or video or [None])[0]


def get_media(entry_id: str, source: Optional[SourceRef], *,
              workspace: Optional[Workspace] = None,
              want_video: bool = False,
              audio_path: Optional[str] = None) -> Media:
    """Fetch (or reuse) the source media for an entry.

    ``want_video`` only matters when the source actually has video; asking
    for video from an audio-only source is not an error — the caller checks
    ``has_video`` on the result and communicates capability up front.
    """
    ws = workspace or Workspace()
    cache = _cache_dir(ws, entry_id)

    if audio_path:
        p = Path(audio_path)
        # Named audio_path for history, but users hand it video files too —
        # judge by what it is, not what the parameter is called.
        return Media(path=p, has_video=p.suffix.lower() in _VIDEO_EXTS,
                     cached=False)

    want_video = want_video and source_has_video(source)
    hit = _find_cached(cache, want_video=want_video)
    if hit is not None:
        return Media(path=hit, has_video=hit.suffix.lower() in _VIDEO_EXTS,
                     cached=True)

    url = None
    if source is not None:
        url = source.audio_url or source.url
    if not url:
        raise ValueError("no media source for this episode; pass audio_path=")

    cache.mkdir(parents=True, exist_ok=True)
    if want_video:
        path = _download_video(url, cache)
        if path is not None:
            return Media(path=path, has_video=True, cached=False)
        # Fall through: no video obtainable, degrade to audio *explicitly*
        # (the caller sees has_video=False and says so before delivering).

    from .asr import download_audio

    audio = Path(download_audio(url, dest_dir=str(cache)))
    named = audio.with_name(f"media{audio.suffix}")
    if audio != named:
        audio.replace(named)
    return Media(path=named,
                 has_video=named.suffix.lower() in _VIDEO_EXTS, cached=False)


def _download_video(url: str, dest_dir: Path) -> Optional[Path]:
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        return None
    out_tmpl = str(dest_dir / "media.%(ext)s")
    opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": out_tmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "merge_output_format": "mp4",
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info))
    except Exception:
        return None
    if path.suffix.lower() not in _VIDEO_EXTS:
        path = path.with_suffix(".mp4")
    return path if path.exists() else None
