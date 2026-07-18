"""Cues → readable paragraphs.

Caption cues are display fragments, not units of meaning — 4 to 9 words,
broken mid-sentence by an encoder optimizing for a two-line overlay. No
human-facing surface renders a ``Cue`` directly (TM-1); cues are merged into
paragraphs first, always.

This module is the engine's ONLY merge implementation — ``transcript.md``,
any future engine-side rendering, and the player's transcript panel follow
the same rules, so they can never disagree about where a paragraph starts.
The rules and constants are the normative ones from
``OpenPod_Transcript_Markdown_Spec.md`` §3 (TM-1..TM-6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import Cue

SENTENCE_END = (".", "!", "?", '."', '?"', '!"', ".'", "…")

SOFT_MIN = 20.0   # start looking for a sentence end after this many seconds
HARD_MAX = 42.0   # force a break here even mid-sentence
GAP_BREAK = 0.9   # a pause this long is a paragraph break if past SOFT_MIN/2


@dataclass
class Paragraph:
    """A reading-sized run of transcript, with its head timestamp."""

    start: float
    end: float
    text: str
    speaker: Optional[str] = None
    cues: int = 0
    # Index into the chapter list (assign_chapters); None = no chapters.
    chapter: Optional[int] = None


def _ends_sentence(text: str) -> bool:
    return text.rstrip().endswith(SENTENCE_END)


def paragraphs(cues: list[Cue],
               chapter_starts: Optional[list[float]] = None) -> list[Paragraph]:
    """Group cues into paragraphs of ~20-42s, broken on sentence ends,
    speaker changes, long pauses, and chapter boundaries.

    The cadence is an outcome, not a target (TM-3): the paragraph is the
    unit, and the ~20s badge rhythm falls out of sentence-boundary merging.
    Short turns stay short (TM-4) — ``SOFT_MIN`` is a floor on merging, not
    on paragraph length. The words are never altered (TM-5): cue text joins
    with single spaces, nothing is corrected or cleaned.
    """
    starts = sorted(chapter_starts or [])
    out: list[Paragraph] = []
    buf: list[Cue] = []

    def flush() -> None:
        if not buf:
            return
        last = buf[-1]
        out.append(Paragraph(
            start=buf[0].start,
            end=last.end if last.end is not None else last.start,
            speaker=buf[0].speaker,
            text=" ".join(c.text.strip() for c in buf if c.text.strip()),
            cues=len(buf),
        ))
        buf.clear()

    for cue in cues:
        prev = buf[-1] if buf else None
        if prev is not None:
            prev_end = prev.end if prev.end is not None else prev.start
            dur = prev_end - buf[0].start
            gap = cue.start - prev_end
            crossed_chapter = any(prev.start < cs <= cue.start for cs in starts)
            speaker_changed = cue.speaker != prev.speaker

            if (speaker_changed
                    or crossed_chapter
                    or (dur >= SOFT_MIN and _ends_sentence(prev.text))
                    or (dur >= SOFT_MIN / 2 and gap >= GAP_BREAK
                        and _ends_sentence(prev.text))
                    or dur >= HARD_MAX):
                flush()
        buf.append(cue)
    flush()
    return out


def assign_chapters(paras: list[Paragraph],
                    chapter_starts: list[float]) -> list[Paragraph]:
    """Stamp each paragraph with the index of the last chapter whose start
    is at or before the paragraph's start (TM-6)."""
    for p in paras:
        idx = 0
        for i, cs in enumerate(chapter_starts):
            if p.start >= cs - 0.01:
                idx = i
        p.chapter = idx
    return paras
