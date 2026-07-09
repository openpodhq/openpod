"""Captions — generated from the transcript, verified for coverage.

The QA rule this module enforces: caption lines are derived from
``transcript.window(start, end)``, never hand-summarized, and any caption
set headed for a burn-in must pass a **coverage check** against that same
window first. (The session that motivated this compressed 17 spoken beats
into 16 and silently lost three claims.)

Translation stays the agent's job — OpenPod ships no translator — but the
contract makes it safe: ``export_captions`` emits source-language lines with
locked timings and ``translation_needed`` set; the agent translates *line by
line* (timings untouched), and ``verify_coverage`` / ``parse_srt`` re-check
the result before any burn. A dropped line is a failed check, not a silent
gap in the published clip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .models import Transcript


@dataclass
class CaptionLine:
    start: float          # seconds, clip-relative (t=0 at clip start)
    end: float
    text: str


@dataclass
class CoverageReport:
    """Do the caption lines cover every spoken cue in the window?"""

    total_cues: int
    covered_cues: int
    gaps: list = field(default_factory=list)   # [{start, end, text}] uncovered
    ok: bool = True

    @property
    def ratio(self) -> float:
        return 1.0 if not self.total_cues else self.covered_cues / self.total_cues

    def to_dict(self) -> dict:
        return {"total_cues": self.total_cues, "covered_cues": self.covered_cues,
                "ratio": round(self.ratio, 3), "ok": self.ok, "gaps": self.gaps}


@dataclass
class CaptionResult:
    lines: list
    language: Optional[str]        # language of the emitted lines (source)
    requested_language: Optional[str]
    translation_needed: bool
    coverage: CoverageReport
    path: Optional[Path] = None    # sidecar file, when written

    def to_dict(self) -> dict:
        return {
            "path": str(self.path) if self.path else None,
            "language": self.language,
            "requested_language": self.requested_language,
            "translation_needed": self.translation_needed,
            "coverage": self.coverage.to_dict(),
            "lines": len(self.lines),
        }


def captions_for_window(transcript: Transcript, start: float,
                        end: float) -> list[CaptionLine]:
    """Caption lines straight from the transcript cues — full coverage by
    construction, re-based so the clip starts at 0."""
    lines = []
    for cue in transcript.window(start, end):
        c_end = cue.end if cue.end is not None else min(cue.start + 5.0, end)
        s = max(cue.start, start) - start
        e = max(min(c_end, end) - start, s + 0.5)
        text = cue.text.strip()
        if text:
            lines.append(CaptionLine(start=round(s, 3), end=round(e, 3),
                                     text=text))
    return lines


def verify_coverage(transcript: Transcript, start: float, end: float,
                    lines: list) -> CoverageReport:
    """Check that every spoken cue in [start, end] is represented by at
    least one caption line (clip-relative timings). Timing-based on purpose:
    it works for translated text where token overlap can't."""
    cues = [c for c in transcript.window(start, end) if c.text.strip()]
    gaps = []
    covered = 0
    for cue in cues:
        c_start = max(cue.start, start) - start
        c_end = (cue.end if cue.end is not None else cue.start + 5.0)
        c_end = min(c_end, end) - start
        mid = (c_start + max(c_end, c_start)) / 2
        hit = any(l.start <= mid <= l.end or
                  (l.start < c_end and l.end > c_start) for l in lines)
        if hit:
            covered += 1
        else:
            gaps.append({"start": round(c_start, 3), "end": round(c_end, 3),
                         "text": cue.text.strip()})
    return CoverageReport(total_cues=len(cues), covered_cues=covered,
                          gaps=gaps, ok=not gaps)


def resolve_caption_language(workspace, transcript: Transcript,
                             lang: Optional[str] = None) -> Optional[str]:
    """The language captions should END UP in: explicit arg → the
    ``clip.caption_language`` setting (``preferred`` resolves through
    ``locale.preferred_language``) → the transcript's own language."""
    if lang and lang not in ("preferred", "source"):
        return lang
    settings = workspace.effective_settings()
    mode = lang or settings["clip"]["caption_language"]
    if mode == "source":
        return transcript.language
    if mode == "preferred":
        return (settings["locale"]["preferred_language"]
                or transcript.language
                or settings["locale"]["fallback_language"])
    return mode


def export_captions(entry, start: float, end: float, *,
                    lang: Optional[str] = None,
                    fmt: str = "srt",
                    out_dir: Optional[Path] = None) -> CaptionResult:
    """Emit a caption sidecar for a clip window + its coverage report.

    Lines are always in the transcript's source language with locked
    timings; when the resolved target language differs,
    ``translation_needed`` tells the agent to translate line-by-line and
    re-verify — never to re-time or summarize.
    """
    transcript = entry.read_transcript()
    if transcript is None or not len(transcript):
        raise ValueError(f"entry {entry.entry_id!r} has no transcript to caption")

    target_lang = resolve_caption_language(entry.workspace, transcript, lang)
    source_lang = transcript.language
    lines = captions_for_window(transcript, start, end)
    coverage = verify_coverage(transcript, start, end, lines)

    dest = Path(out_dir) if out_dir else entry.clips_dir
    dest.mkdir(parents=True, exist_ok=True)
    suffix = f".{source_lang}" if source_lang else ""
    path = dest / f"{int(start)}-{int(end)}{suffix}.{fmt}"
    path.write_text(to_vtt(lines) if fmt == "vtt" else to_srt(lines),
                    encoding="utf-8")

    # Unknown source language + explicit target still flags translation:
    # the agent verifies what language the lines actually are — we don't guess.
    return CaptionResult(
        lines=lines, language=source_lang, requested_language=target_lang,
        translation_needed=bool(target_lang) and source_lang != target_lang,
        coverage=coverage, path=path)


# --------------------------------------------------------------------------- #
# Phrase chunking — captions are data first, pixels later
# --------------------------------------------------------------------------- #

# Force-break marker for translated lines (esp. Hebrew): an agent translating
# a phrase inserts ‖ where the visual break must stay, so downstream chunking
# never regroups phrases it can't linguistically judge.
FORCE_BREAK = "‖"   # ‖

# Right-to-left languages: burn renderers must use RTL-capable shaping and
# dedicated faces (never assume Inter/Arial covers it).
_RTL_LANGS = {"he", "iw", "ar", "fa", "ur", "yi"}


def is_rtl(lang: Optional[str]) -> bool:
    return bool(lang) and lang.split("-")[0].lower() in _RTL_LANGS


_sentence_end = re.compile(r"[.!?…]+[\"')\]]?$")


def split_phrases(text: str, *, max_words: int = 5) -> list[tuple[str, str]]:
    """Chunk text into short caption phrases: ``[(phrase, break_type)]``.

    Rules (the karaoke contract): break at ``‖`` (``"force"``), at sentence
    ends (``"sentence"``), else every ``max_words`` words (``"length"``).
    """
    phrases: list[tuple[str, str]] = []
    for segment in text.split(FORCE_BREAK):
        words = segment.split()
        chunk: list[str] = []
        for w in words:
            chunk.append(w)
            if _sentence_end.search(w):
                phrases.append((" ".join(chunk), "sentence"))
                chunk = []
            elif len(chunk) >= max_words:
                phrases.append((" ".join(chunk), "length"))
                chunk = []
        if chunk:
            phrases.append((" ".join(chunk), "force"))
    if phrases:
        # The trailing chunk of the whole text isn't a forced break — it just
        # ends. Mark it by what actually ended it.
        text_stripped = text.rstrip()
        last_text, last_break = phrases[-1]
        if last_break == "force" and not text_stripped.endswith(FORCE_BREAK):
            phrases[-1] = (last_text,
                           "sentence" if _sentence_end.search(last_text) else "length")
    return phrases


def phrases_for_window(transcript: Transcript, start: float, end: float, *,
                       max_words: int = 5) -> list[dict]:
    """Karaoke-ready phrases for a clip window, clip-relative timings.

    Word timings inside a cue are interpolated by word count — honest enough
    for phrase display; word-accurate highlighting needs a word-level track
    (see :func:`word_timings_for_clip`).
    """
    out: list[dict] = []
    for line in captions_for_window(transcript, start, end):
        words = line.text.split()
        if not words:
            continue
        span = line.end - line.start
        per_word = span / len(words)
        cursor = line.start
        for phrase, brk in split_phrases(line.text, max_words=max_words):
            n = len(phrase.split())
            out.append({
                "text": phrase,
                "start": round(cursor, 3),
                "end": round(cursor + n * per_word, 3),
                "break": brk,
            })
            cursor += n * per_word
    return out


def captions_block(transcript: Transcript, start: float, end: float, *,
                   words: Optional[list] = None,
                   max_words: int = 5) -> dict:
    """The structured caption track embedded in a clip's ``.json`` sidecar.

    ``timing`` is honest: rolling platform cues (YouTube captions drift a
    few seconds and are not word-timed) are ``"approximate"``; word-level
    ASR of the clip window is ``"exact"``.
    """
    approx = (not transcript.word_level) and \
        "youtube" in (transcript.source or "")
    block = {
        "source": "whisper-window" if words else "transcript",
        "timing": "exact" if words else ("approximate" if approx else "cue"),
        "language": transcript.language,
        "rtl": is_rtl(transcript.language),
        "phrases": phrases_for_window(transcript, start, end,
                                      max_words=max_words),
    }
    if words:
        block["words"] = words
    return block


def word_timings_for_clip(media_path, start: float, end: float, *,
                          model: str = "base") -> list[dict]:
    """Word-level timings for a clip window — the karaoke-highlight track.

    Cuts the window from the (cached) source media and runs faster-whisper
    with word timestamps on just those seconds. Needs the ``asr`` extra and
    ffmpeg; raises :class:`~openpod.asr.DependencyMissing` otherwise.
    Timings returned clip-relative.
    """
    import subprocess as _sp
    import tempfile as _tf
    from pathlib import Path as _P

    from .asr import DependencyMissing, has_ffmpeg

    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as e:
        raise DependencyMissing(
            "Word-level captions need faster-whisper: "
            "pip install 'openpod[asr]'") from e
    if not has_ffmpeg():
        raise DependencyMissing("Word-level captions need ffmpeg on PATH.")

    with _tf.TemporaryDirectory(prefix="openpod-words-") as td:
        snippet = _P(td) / "window.wav"
        _sp.run(["ffmpeg", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
                 "-i", str(media_path), "-ac", "1", "-ar", "16000",
                 str(snippet)], capture_output=True, check=True)
        whisper = WhisperModel(model, device="auto", compute_type="auto")
        segments, _info = whisper.transcribe(str(snippet),
                                             word_timestamps=True,
                                             vad_filter=True)
        words = []
        for seg in segments:
            for w in (seg.words or []):
                words.append({"text": w.word.strip(),
                              "start": round(float(w.start), 3),
                              "end": round(float(w.end), 3)})
        return words


# --------------------------------------------------------------------------- #
# Formats
# --------------------------------------------------------------------------- #

def _ts_srt(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ts_vtt(seconds: float) -> str:
    return _ts_srt(seconds).replace(",", ".")


def to_srt(lines: list) -> str:
    blocks = []
    for i, l in enumerate(lines, 1):
        blocks.append(f"{i}\n{_ts_srt(l.start)} --> {_ts_srt(l.end)}\n{l.text}\n")
    return "\n".join(blocks)


def to_vtt(lines: list) -> str:
    blocks = ["WEBVTT\n"]
    for l in lines:
        blocks.append(f"{_ts_vtt(l.start)} --> {_ts_vtt(l.end)}\n{l.text}\n")
    return "\n".join(blocks)


_srt_time = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


def parse_srt(text: str) -> list[CaptionLine]:
    """Parse SRT (or VTT-ish) text back into lines — the re-verify path for
    agent-translated caption files."""
    lines = []
    for block in re.split(r"\n\s*\n", text.strip()):
        m = _srt_time.search(block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        body = block[m.end():].strip()
        body = "\n".join(l for l in body.splitlines() if l.strip())
        if body:
            lines.append(CaptionLine(start=start, end=end, text=body))
    return lines
