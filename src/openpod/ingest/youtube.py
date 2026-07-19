"""YouTube ingestion — captions first, ASR only as an *announced* fallback.

Caption cue timing (±2–4s, drifts early) is good enough for navigation, and
it costs nothing and needs no Whisper. Local ASR is minutes of compute, so
falling back to it is a decision, never a silent default:

The objective stays *fast and simple interaction*, so the gate is
cost-based — notify is cheap, asking is friction:

* **Transient** caption failure (429 / IP throttling) with a **short** ASR
  estimate (under ``auto_threshold``) → just transcribe; a one-line notice
  says why and that it's local compute (no tokens, no cost).
* Transient failure with a **long** estimate → raise
  :class:`~openpod.errors.CaptionsUnavailable` with both options priced
  (wait for free captions vs ``asr="now"`` at ~N min) — that's the only
  case where stopping beats guessing.
* **Permanent** absence (captions disabled / none exist) → ASR proceeds
  (it's the only path), announced via ``progress`` with the estimate, and
  the transcript carries a ``notes`` line.

This module keeps its heavy dependencies (``youtube-transcript-api``,
``yt-dlp``) lazy so the base package installs without them; install the
``youtube`` extra to enable it.
"""

from __future__ import annotations

from typing import Callable, Optional

from ..errors import CaptionsUnavailable
from ..models import SourceRef, Transcript
from .resolve import youtube_video_id

# Exception class names from youtube-transcript-api that mean "this video
# will never have captions" (vs. throttling, which cools off).
_PERMANENT = {"TranscriptsDisabled", "NoTranscriptFound",
              "NoTranscriptAvailable", "VideoUnavailable", "VideoUnplayable"}
_TRANSIENT = {"TooManyRequests", "IpBlocked", "RequestBlocked",
              "YouTubeRequestFailed", "CouldNotRetrieveTranscript"}


def ingest_youtube(url: str, *, prefer_captions: bool = True,
                   languages: Optional[list[str]] = None,
                   asr: str = "auto",
                   auto_threshold: float = 180.0,
                   progress: Optional[Callable[[str], None]] = None
                   ) -> tuple[SourceRef, Transcript]:
    """Resolve a YouTube URL to (source, transcript).

    ``asr``: ``"auto"`` (captions first; ASR runs — announced — whenever
    captions can never exist *or* the transcription is cheap enough that
    asking would cost more than doing), ``"now"`` (user approved — proceed
    on any caption failure), ``"never"`` (raise instead of ever
    transcribing). ``auto_threshold`` is the high-estimate cutoff, in
    seconds, for auto-proceeding after a transient caption failure.
    ``progress`` is called with a human line before any ASR run starts.
    """
    if asr not in ("auto", "now", "never"):
        raise ValueError(f"asr must be auto|now|never, got {asr!r}")
    video_id = youtube_video_id(url)
    if not video_id:
        raise ValueError(f"could not extract a YouTube video id from: {url}")

    source = SourceRef(kind="youtube", url=url, video_id=video_id)
    _enrich_metadata(source)

    failure = detail = None
    if prefer_captions:
        t, failure, detail = _captions(video_id, languages or ["en"])
        if t is not None and len(t):
            return source, t
    else:
        failure, detail = "skipped", "prefer_captions=False"

    from ..asr import estimate_transcription

    estimate = estimate_transcription(source.duration)

    if asr == "never":
        raise CaptionsUnavailable(url, reason=failure or "unavailable",
                                  detail=detail, estimate=estimate)
    if failure in ("transient", "rate_limited") and asr != "now":
        # Cost-based gate: a short transcription just runs (asking costs
        # more than doing); only a genuinely long one — where waiting out
        # the throttle is plausibly faster — surfaces the choice.
        high = estimate.get("high_seconds")
        cheap = high is not None and high <= auto_threshold
        if not cheap:
            raise CaptionsUnavailable(url, reason=failure, detail=detail,
                                      estimate=estimate)

    reason_line = {
        "permanent": "this video has no captions",
        "rate_limited": "captions are temporarily rate-limited by YouTube",
        "transient": "captions are temporarily unavailable",
        "skipped": "captions skipped (--force-asr)",
    }.get(failure or "permanent", "captions unavailable")
    note = (f"{reason_line}; transcribed locally "
            f"(~{estimate.get('human', '?')} expected)")
    if failure in ("transient", "rate_limited"):
        note += " — a later re-catch may upgrade this to platform captions"
    if progress:
        progress(f"{reason_line} — transcribing locally instead "
                 f"(~{estimate.get('human', 'several minutes')}; runs on this "
                 "machine, no tokens or API cost)")

    from ..asr import transcribe, download_audio

    audio = download_audio(url)
    t = transcribe(audio)
    t.notes = note
    return source, t


def _captions(video_id: str, languages: list[str]
              ) -> tuple[Optional[Transcript], Optional[str], Optional[str]]:
    """Fetch captions; on failure, say *what kind* of failure.

    Returns ``(transcript, failure, detail)`` where failure is None on
    success, ``"permanent"`` (captions will never exist), ``"rate_limited"``
    (429-class throttling) or ``"transient"`` (worth retrying).
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
    except ImportError:
        return None, "permanent", ("youtube-transcript-api not installed "
                                   '(pip install "openpod[youtube]")')
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
    except Exception as e:
        return None, *_classify(e)

    from .. import transcript as tx

    cues = tx.from_cue_dicts(fetched)
    return (Transcript(cues=cues, source="youtube-captions",
                       language=languages[0]), None, None)


def _classify(e: Exception) -> tuple[str, str]:
    """Map a caption-fetch exception to (failure_kind, detail)."""
    name = type(e).__name__
    text = f"{name}: {e}"
    if name in _PERMANENT:
        return "permanent", text
    if "429" in str(e) or name in ("TooManyRequests", "IpBlocked",
                                   "RequestBlocked"):
        return "rate_limited", text
    if name in _TRANSIENT:
        return "transient", text
    # Unknown failures are treated as transient: wrongly retrying is cheap,
    # wrongly burning a 20-minute Whisper run silently is not.
    return "transient", text


def _enrich_metadata(source: SourceRef) -> None:
    """Best-effort title/show/duration/chapters via yt-dlp (metadata only, no
    download). Chapters are the creator's own segment boundaries — the anchors
    deep-links should prefer — taken from YouTube's chapter markers or, when
    absent, parsed out of the description's timestamp lines."""
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
        chapters = info.get("chapters")
        if not chapters and info.get("description"):
            from ..segments import parse_description_chapters

            chapters = parse_description_chapters(info["description"],
                                                  source.duration)
        source.chapters = chapters or source.chapters
    except Exception:
        return
