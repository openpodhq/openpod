"""Parse the many timed-transcript formats into a single :class:`Transcript`.

Supported inputs:

* WebVTT (``.vtt``) — podcast:transcript, YouTube ``--sub-format vtt``
* SubRip (``.srt``)
* YouTube ``json3`` caption JSON (word/segment events)
* youtube-transcript-api cue lists (``{"text","start","duration"}``)
* a plain list of ``{"start","end","text"}`` dicts

Everything normalises to seconds. Caption-cue timing is good enough for
navigation; only clip *cutting* needs word-accurate timing (see ``asr``/``clip``).
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from .models import Cue, Transcript

# --------------------------------------------------------------------------- #
# Timestamp helpers
# --------------------------------------------------------------------------- #

_TS = re.compile(
    r"(?:(?P<h>\d+):)?(?P<m>\d{1,2}):(?P<s>\d{2})(?:[.,](?P<ms>\d{1,3}))?"
)


def parse_timestamp(text: str) -> float:
    """Parse ``HH:MM:SS.mmm`` / ``MM:SS,mmm`` -> seconds."""
    m = _TS.search(text.strip())
    if not m:
        raise ValueError(f"unparseable timestamp: {text!r}")
    h = int(m.group("h") or 0)
    mins = int(m.group("m"))
    secs = int(m.group("s"))
    ms = m.group("ms")
    frac = int(ms.ljust(3, "0")) / 1000 if ms else 0.0
    return h * 3600 + mins * 60 + secs + frac


# --------------------------------------------------------------------------- #
# VTT / SRT
# --------------------------------------------------------------------------- #

_CUE_TIMING = re.compile(
    r"(?P<start>[\d:.,]+)\s*-->\s*(?P<end>[\d:.,]+)"
)
_TAG = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    text = _TAG.sub("", text)          # strip <c>, <00:00:01.000> inline tags
    return " ".join(text.split())


def parse_vtt(data: str) -> list[Cue]:
    cues: list[Cue] = []
    block: list[str] = []

    def flush() -> None:
        if not block:
            return
        timing_idx = next(
            (i for i, ln in enumerate(block) if "-->" in ln), None
        )
        if timing_idx is None:
            return
        m = _CUE_TIMING.search(block[timing_idx])
        if not m:
            return
        text = _clean(" ".join(block[timing_idx + 1:]))
        if text:
            cues.append(
                Cue(
                    start=parse_timestamp(m.group("start")),
                    end=parse_timestamp(m.group("end")),
                    text=text,
                )
            )

    for line in data.splitlines():
        if line.strip() == "":
            flush()
            block = []
        elif line.strip().upper().startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        else:
            block.append(line)
    flush()
    return _dedupe(cues)


def parse_srt(data: str) -> list[Cue]:
    # SRT is VTT-shaped once you ignore the numeric index lines.
    return parse_vtt(data)


# --------------------------------------------------------------------------- #
# YouTube json3
# --------------------------------------------------------------------------- #


def parse_json3(data: str | dict) -> list[Cue]:
    doc = json.loads(data) if isinstance(data, str) else data
    cues: list[Cue] = []
    for event in doc.get("events", []):
        segs = event.get("segs")
        if not segs or "tStartMs" not in event:
            continue
        text = _clean("".join(s.get("utf8", "") for s in segs))
        if not text:
            continue
        start = event["tStartMs"] / 1000.0
        dur = event.get("dDurationMs")
        end = start + dur / 1000.0 if dur else None
        cues.append(Cue(start=start, end=end, text=text))
    return _dedupe(cues)


# --------------------------------------------------------------------------- #
# Cue lists (youtube-transcript-api and generic dicts)
# --------------------------------------------------------------------------- #


def from_cue_dicts(items: Iterable[dict[str, Any]]) -> list[Cue]:
    """Accept ``{"text","start","duration"}`` or ``{"start","end","text"}``."""
    cues: list[Cue] = []
    for it in items:
        start = float(it["start"])
        if "end" in it and it["end"] is not None:
            end: float | None = float(it["end"])
        elif "duration" in it and it["duration"] is not None:
            end = start + float(it["duration"])
        else:
            end = None
        text = _clean(str(it.get("text", "")))
        if text:
            cues.append(Cue(start=start, end=end, text=text, speaker=it.get("speaker")))
    return _dedupe(cues)


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #


def load(data: str, *, fmt: str | None = None, source: str = "unknown",
         language: str | None = None, word_level: bool = False) -> Transcript:
    """Parse ``data`` into a :class:`Transcript`, sniffing the format if needed."""
    fmt = (fmt or _sniff(data)).lower()
    if fmt in ("vtt", "webvtt"):
        cues = parse_vtt(data)
    elif fmt == "srt":
        cues = parse_srt(data)
    elif fmt == "json3":
        cues = parse_json3(data)
    else:
        raise ValueError(f"unsupported transcript format: {fmt!r}")
    return Transcript(cues=cues, source=source, language=language, word_level=word_level)


def load_file(path, *, source: str | None = None, **kw) -> Transcript:
    from pathlib import Path

    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    fmt = {
        ".vtt": "vtt", ".srt": "srt", ".json3": "json3", ".json": "json3",
    }.get(p.suffix.lower())
    return load(text, fmt=fmt, source=source or f"file:{p.name}", **kw)


def _sniff(data: str) -> str:
    head = data.lstrip()[:200].upper()
    if head.startswith("WEBVTT"):
        return "vtt"
    if head.startswith("{") and '"EVENTS"' in data[:2000].upper():
        return "json3"
    if "-->" in data[:2000]:
        # SRT blocks start with an integer index line.
        first = data.lstrip().splitlines()[0].strip()
        return "srt" if first.isdigit() else "vtt"
    raise ValueError("could not sniff transcript format; pass fmt=")


def _dedupe(cues: list[Cue]) -> list[Cue]:
    """Drop consecutive duplicate lines (common in YouTube rolling captions)."""
    out: list[Cue] = []
    for c in cues:
        if out and out[-1].text == c.text and abs(out[-1].start - c.start) < 0.05:
            continue
        out.append(c)
    return out
