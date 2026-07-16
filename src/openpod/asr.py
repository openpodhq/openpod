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

_UA = {"User-Agent": "OpenPod/0.1 (+https://github.com/openpodhq/openpod)"}


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

    # Direct enclosure: validate the content type, then fetch the terminal
    # URL (tracking redirectors resolved once, not on every request).
    fetch_url, ext = url, None
    if url.lower().split("?")[0].endswith((".mp3", ".m4a", ".aac", ".ogg", ".wav")):
        ext = url.lower().split("?")[0].rsplit(".", 1)[-1]
    elif url.lower().startswith(("http://", "https://")):
        # No recognizable extension (e.g. a chrt.fm-style redirector): sniff
        # what it actually serves instead of guessing.
        from .ingest.validate import sniff

        info = sniff(url)
        if info.kind == "audio":
            fetch_url = info.url
            ext = (info.content_type or "audio/mpeg").split("/")[-1].split(";")[0]
            ext = {"mpeg": "mp3", "mp4": "m4a", "x-m4a": "m4a"}.get(ext, ext)

    if ext:
        out = Path(dest_dir) / f"audio.{ext}"
        req = urllib.request.Request(fetch_url, headers=_UA)
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


def estimate_transcription(duration: Optional[float],
                           model: str = "base") -> dict:
    """Rough wall-clock estimate for local Whisper on typical hardware.

    Deliberately a *range* — CPU/GPU spread is wide. The point is honesty
    at the moment of decision ("~9–25 min for this episode"), not precision.
    """
    # Realtime factors per model size (fraction of audio duration), low/high.
    factors = {"tiny": (0.03, 0.10), "base": (0.05, 0.20),
               "small": (0.10, 0.35), "medium": (0.25, 0.80),
               "large": (0.5, 1.5)}
    lo_f, hi_f = factors.get(model.split("-")[0], (0.05, 0.20))
    if not duration:
        return {"model": model, "human": "several minutes (unknown duration)"}
    lo, hi = duration * lo_f, duration * hi_f

    def _m(s: float) -> str:
        return f"{max(1, round(s / 60))} min" if s >= 45 else f"{round(s)}s"

    return {"model": model, "low_seconds": round(lo), "high_seconds": round(hi),
            "human": f"{_m(lo)}–{_m(hi)}"}


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


_ffmpeg_caps: Optional[dict] = None


def ffmpeg_capabilities(*, refresh: bool = False) -> dict:
    """What this ffmpeg build can actually do, probed once.

    Homebrew builds routinely ship without libass/drawtext; anything that
    promises burned-in text must check here first and degrade honestly
    (sidecar captions) instead of failing mid-encode.
    """
    global _ffmpeg_caps
    if _ffmpeg_caps is not None and not refresh:
        return _ffmpeg_caps
    caps = {"ffmpeg": has_ffmpeg(), "subtitles": False, "drawtext": False}
    if caps["ffmpeg"]:
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                                 capture_output=True, text=True, timeout=15).stdout
            caps["subtitles"] = " subtitles " in out
            caps["drawtext"] = " drawtext " in out
        except Exception:
            pass
    _ffmpeg_caps = caps
    return caps
