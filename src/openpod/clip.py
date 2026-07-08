"""``clip`` — precise, local moment extraction.

Cuts a word-ish-accurate span out of an episode's audio into a **local file the
user owns**. No publishing, no re-hosting — that regulated surface is Stage 2.
Boundaries snap to transcript cue edges (the naval-clipper trick) so cuts land
on sentence boundaries rather than mid-word.

Requires ``ffmpeg`` on PATH and access to the source audio (a podcast enclosure,
or a downloadable media URL via the ``youtube`` extra).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .asr import has_ffmpeg
from .card import render_card_html, render_card_png
from .config import Workspace
from .deeplink import build_deeplink
from .library import Library, LibraryEntry
from .models import Transcript, format_timestamp, slugify


@dataclass
class ClipResult:
    path: Path
    start: float
    end: float
    quote: str
    deeplink: Optional[str]
    card_path: Optional[Path] = None       # card.html — always written when make_card
    card_png_path: Optional[Path] = None   # card.png — best-effort, needs openpod[card-png]
    has_video: bool = False                # the produced clip carries video
    capability_note: Optional[str] = None  # honest degrade, stated up front


def snap_to_cues(transcript: Transcript, start: float, end: float,
                 *, pad: float = 0.0) -> tuple[float, float]:
    """Expand [start, end] outward to the nearest enclosing cue boundaries."""
    if not len(transcript):
        return max(0.0, start - pad), end + pad
    starts = [c.start for c in transcript.cues]
    # snap start down to the cue that contains it
    snapped_start = max((s for s in starts if s <= start), default=starts[0])
    # snap end up to the end of the cue containing `end`
    snapped_end = end
    for c in transcript.cues:
        c_end = c.end if c.end is not None else c.start
        if c.start <= end <= max(c_end, c.start):
            snapped_end = max(c_end, end)
            break
    else:
        later = [c.start for c in transcript.cues if c.start >= end]
        if later:
            snapped_end = later[0]
    return max(0.0, snapped_start - pad), snapped_end + pad


def clip(entry_id_or_link: str, start: float, end: float, *,
         workspace: Optional[Workspace] = None, snap: bool = True,
         audio_path: Optional[str] = None, reencode: bool = False,
         make_card: bool = True, video: Optional[bool] = None) -> ClipResult:
    """Cut a clip. ``video=None`` (default) preserves video whenever the
    source has it; ``video=False`` forces audio-only; ``video=True`` demands
    video and reports honestly (``capability_note``) when the source is
    audio-only — *before* a mismatched deliverable, not after."""
    if end <= start:
        raise ValueError("end must be greater than start")
    if not has_ffmpeg():
        raise RuntimeError(
            "clip requires ffmpeg on your PATH. Install it (e.g. `brew install ffmpeg`)."
        )

    ws = (workspace or Workspace())
    library = Library(ws)
    entry = library.get(entry_id_or_link)
    if entry is None:
        raise ValueError(
            f"no caught episode with id {entry_id_or_link!r}. "
            "Run `openpod catch <link>` first, then clip by its entry id."
        )

    transcript = entry.read_transcript()
    source = entry.source()
    if snap and transcript is not None:
        start, end = snap_to_cues(transcript, start, end)

    quote = ""
    if transcript is not None:
        quote = " ".join(c.text for c in transcript.window(start, end)).strip()

    # Get the media — via the shared cache, video-preserving by default.
    from .media import get_media, source_has_video

    want_video = source_has_video(source) if video is None else video
    m = get_media(entry.entry_id, source, workspace=ws,
                  want_video=want_video, audio_path=audio_path)
    media = str(m.path)

    capability_note = None
    if want_video and not m.has_video:
        capability_note = (
            "this source provides no video track — the clip is audio-only")

    entry.clips_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{int(start)}-{int(end)}-{slugify(quote[:40], fallback='clip')}"
    ext = Path(media).suffix or ".m4a"
    out = entry.clips_dir / f"{stem}{ext}"

    _ffmpeg_cut(media, start, end, out, reencode=reencode)

    deeplink = build_deeplink(source, start) if source else None
    meta = {
        "start": start, "end": end, "quote": quote,
        "deeplink": deeplink, "source": source.to_dict() if source else None,
    }
    (entry.clips_dir / f"{stem}.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    card_path = None
    card_png_path = None
    if make_card and source is not None and quote:
        card_path = entry.clips_dir / f"{stem}.card.html"
        card_path.write_text(render_card_html(source, start, quote, deeplink),
                             encoding="utf-8")
        png_candidate = entry.clips_dir / f"{stem}.card.png"
        if render_card_png(card_path, png_candidate):
            card_png_path = png_candidate

    return ClipResult(path=out, start=start, end=end, quote=quote,
                     deeplink=deeplink, card_path=card_path,
                     card_png_path=card_png_path,
                     has_video=m.has_video and out.suffix.lower() in
                     (".mp4", ".mkv", ".webm", ".mov"),
                     capability_note=capability_note)


def _ffmpeg_cut(media: str, start: float, end: float, out: Path,
                *, reencode: bool) -> None:
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", media]
    if reencode:
        cmd += ["-c:a", "aac", "-b:a", "160k"]
    else:
        cmd += ["-c", "copy"]
    cmd += [str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 and not reencode:
        # Stream copy can fail on some containers; retry with a re-encode.
        _ffmpeg_cut(media, start, end, out, reencode=True)
        return
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[-500:]}")
