"""Local ASR fallback and audio download.

Transcription runs on the user's own hardware via ``faster-whisper`` (install the
``asr`` extra). This keeps the free path at $0 company COGS — no audio ever
leaves the machine. Both helpers raise a clear, actionable error when their
optional dependency / system tool is missing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional

from .models import Cue, Transcript

_UA = {"User-Agent": "OpenPod/0.1 (+https://github.com/openpod/openpod)"}


class DependencyMissing(RuntimeError):
    """Raised when an optional dependency needed for a path isn't installed."""


def transcribe(audio_path: str, *, model: str = "base",
               language: Optional[str] = None) -> Transcript:
    """Transcribe a local audio/video file with faster-whisper.

    Produces segment-level cues (good for navigation). For frame-accurate clip
    cutting, ``clip`` re-aligns a narrow window at higher precision.
    """
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as e:  # pragma: no cover - depends on env
        raise DependencyMissing(
            "Local transcription needs faster-whisper. Install it with:\n"
            "    pip install 'openpod[asr]'\n"
            "or supply a transcript directly with --transcript."
        ) from e

    model_name = os.environ.get("OPENPOD_WHISPER_MODEL", model)
    whisper = WhisperModel(model_name, device="auto", compute_type="auto")
    segments, info = whisper.transcribe(audio_path, language=language, vad_filter=True)
    cues = [
        Cue(start=float(s.start), end=float(s.end), text=s.text.strip())
        for s in segments
        if s.text.strip()
    ]
    return Transcript(
        cues=cues,
        source=f"asr:whisper-{model_name}",
        language=getattr(info, "language", language),
        word_level=False,
    )


def download_audio(url: str, *, dest_dir: Optional[str] = None) -> str:
    """Download audio to a local file, returning its path.

    Uses ``yt-dlp`` for streaming sites (YouTube etc.) and a plain HTTP GET for
    direct podcast enclosures.
    """
    dest_dir = dest_dir or tempfile.mkdtemp(prefix="openpod-audio-")
    Path(dest_dir).mkdir(parents=True, exist_ok=True)

    # Direct enclosure: just fetch it.
    if url.lower().split("?")[0].endswith((".mp3", ".m4a", ".aac", ".ogg", ".wav")):
        ext = url.lower().split("?")[0].rsplit(".", 1)[-1]
        out = Path(dest_dir) / f"audio.{ext}"
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=120) as resp, out.open("wb") as fh:  # noqa: S310
            shutil.copyfileobj(resp, fh)
        return str(out)

    # Otherwise defer to yt-dlp.
    try:
        import yt_dlp  # type: ignore
    except ImportError as e:
        raise DependencyMissing(
            "Downloading from this source needs yt-dlp. Install it with:\n"
            "    pip install 'openpod[youtube]'"
        ) from e

    out_tmpl = str(Path(dest_dir) / "audio.%(ext)s")
    opts = {
        "format": "bestaudio/best",
        "outtmpl": out_tmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None
