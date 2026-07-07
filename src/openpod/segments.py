"""Segment an episode into the coherent beats deep-links should land on.

A deep-link to the caption cue where a word was *said* drops the listener
mid-sentence. The link the product promises is the start of the **beat** —
the stretch of conversation the moment belongs to. Boundary sources, in order
of trust:

1. **Creator chapters.** If the video ships chapters (YouTube chapter markers
   or a timestamp list in the description), those are the author's own claim
   about structure — use them verbatim.
2. **Local topic segmentation.** Otherwise detect boundaries from the
   transcript itself: lexical-cohesion dips between adjacent windows
   (TextTiling-style), reinforced by silence gaps between cues. Deterministic,
   offline, no model.

The two are hybridised into the **beats layer**: a creator chapter that runs
very long (a 15-minute closing Q&A) is too coarse an anchor — every idea
inside it would deep-link to the same far-away chapter start. Chapters longer
than ``_SPLIT_CHAPTER_SECS`` get the topic detector run *within* their window
and contribute its sub-segments (origin ``"topic"``, titles prefixed with the
parent chapter title). Shorter chapters pass through verbatim.

But readers want different landings at different times, so nothing is thrown
away: every idea, search hit, and TOC entry carries the full **anchor
ladder** — the creator's *chapter* ("take me to the topic"), the detected
*beat* ("play the argument from where it starts being articulated"), and the
exact *moment* ("show me where they said it") — each labeled, so the user
picks the granularity, not us.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Optional

from .deeplink import build_deeplink
from .models import Segment, SourceRef, Transcript


def segment_transcript(transcript: Transcript,
                       chapters: Optional[list] = None) -> list[Segment]:
    """The one entry point: creator chapters when present, topic detection
    otherwise. Always returns at least one segment covering the episode."""
    duration = transcript.duration
    if chapters:
        segs = _from_chapters(chapters, duration)
        if segs:
            return _split_long_chapters(segs, transcript)
    return _topic_segments(transcript)


def segment_at(segments: list[Segment], seconds: float) -> Optional[Segment]:
    """The segment containing ``seconds`` (the last one starting at/before it)."""
    chosen = None
    for seg in segments:
        if seg.start <= seconds:
            chosen = seg
        else:
            break
    return chosen


def annotate(items: list, segments: list[Segment],
             source: Optional[SourceRef],
             chapters: Optional[list[Segment]] = None) -> None:
    """Attach the anchor ladder to Ideas / SearchHits in place.

    ``segments`` supplies the beat anchor (where the idea starts being
    articulated); ``chapters`` supplies the creator's chapter anchor. When
    the two coincide (a short chapter that *is* the beat), the chapter fields
    are left unset — one link, not the same link twice."""
    for item in items:
        seg = segment_at(segments, item.start) if segments else None
        if seg is not None:
            item.segment_start = seg.start
            item.segment_title = seg.title
            if source is not None:
                item.segment_deeplink = build_deeplink(source, seg.start)
        ch = segment_at(chapters, item.start) if chapters else None
        if ch is not None and (seg is None or ch.start != seg.start):
            item.chapter_start = ch.start
            item.chapter_title = ch.title
            if source is not None:
                item.chapter_deeplink = build_deeplink(source, ch.start)


# --------------------------------------------------------------------------- #
# Creator chapters
# --------------------------------------------------------------------------- #

# "0:00 Intro", "00:12:30 - The 10x paradox", "[1:23:45] Q&A" …
_CHAPTER_LINE = re.compile(
    r"^\s*[\[\(]?(\d{1,2}:)?(\d{1,3}):(\d{2})[\]\)]?\s*[-–—:·]?\s+(\S.*)$"
)


def parse_description_chapters(description: str,
                               duration: Optional[float] = None
                               ) -> list[dict]:
    """Extract a chapter list from a video description's timestamp lines.

    Follows YouTube's own rules for what counts as chapters: at least three
    entries, starting at 0:00, in ascending order — anything less is just a
    couple of timestamps in prose, not structure."""
    found: list[dict] = []
    for line in description.splitlines():
        m = _CHAPTER_LINE.match(line)
        if not m:
            continue
        h, mnt, sec, title = m.groups()
        seconds = (int(h.rstrip(":")) if h else 0) * 3600 + int(mnt) * 60 + int(sec)
        found.append({"start": float(seconds), "title": title.strip()})

    if len(found) < 3 or found[0]["start"] > 5.0:
        return []
    starts = [c["start"] for c in found]
    if starts != sorted(starts) or len(set(starts)) != len(starts):
        return []
    for prev, nxt in zip(found, found[1:]):
        prev["end"] = nxt["start"]
    if duration:
        found[-1]["end"] = float(duration)
    return found


def chapters_as_segments(chapters: list,
                         duration: Optional[float] = None) -> list[Segment]:
    """Public wrapper: the creator-chapter layer as verbatim Segments."""
    return _from_chapters(chapters, duration or 0.0)


def _from_chapters(chapters: list, duration: float) -> list[Segment]:
    """Normalize whatever chapter shape we were handed (yt-dlp uses
    start_time/end_time; our own parser uses start/end)."""
    segs: list[Segment] = []
    for ch in chapters:
        start = ch.get("start", ch.get("start_time"))
        if start is None:
            continue
        end = ch.get("end", ch.get("end_time"))
        segs.append(Segment(start=float(start),
                            end=float(end) if end is not None else None,
                            title=ch.get("title"), origin="chapters"))
    segs.sort(key=lambda s: s.start)
    for prev, nxt in zip(segs, segs[1:]):
        if prev.end is None:
            prev.end = nxt.start
    if segs and segs[-1].end is None and duration:
        segs[-1].end = duration
    return segs


_SPLIT_CHAPTER_SECS = 330.0  # chapters longer than this get sub-segmented


def _split_long_chapters(segs: list[Segment],
                         transcript: Transcript) -> list[Segment]:
    """Replace over-long chapters with topic sub-segments found inside them.

    The sub-segments keep the chapter title as a prefix ("Bestie Q&A — <most
    salient line>") so the anchor still names the structure the creator
    declared. A long chapter the detector can't split stays verbatim."""
    out: list[Segment] = []
    for seg in segs:
        if seg.end is None or seg.end - seg.start <= _SPLIT_CHAPTER_SECS:
            out.append(seg)
            continue
        window = Transcript(cues=transcript.window(seg.start, seg.end),
                            source=transcript.source)
        subs = _topic_segments(window)
        if len(subs) < 2:
            out.append(seg)
            continue
        # Snap the sub-segments to the chapter's own bounds: the detector
        # starts at the first cue, which may sit inside the chapter.
        subs[0].start = seg.start
        subs[-1].end = seg.end
        for sub in subs:
            if seg.title:
                sub.title = f"{seg.title} — {sub.title}" if sub.title \
                    else seg.title
        out.extend(subs)
    return out


# --------------------------------------------------------------------------- #
# Local topic segmentation (TextTiling-lite + silence gaps)
# --------------------------------------------------------------------------- #

_BLOCK_SECS = 20.0     # cues are pooled into ~20s blocks before comparison
_WINDOW = 3            # blocks on each side of a candidate boundary
_GAP_BONUS_SECS = 2.0  # a silence this long between cues reinforces a boundary


def _topic_segments(transcript: Transcript) -> list[Segment]:
    duration = transcript.duration
    if not len(transcript) or duration <= 0:
        return []

    blocks = _blocks(transcript)
    if len(blocks) < 2 * _WINDOW + 2:
        return [_titled(Segment(start=transcript.cues[0].start, end=duration),
                        transcript)]

    # Cohesion between the windows either side of each block boundary; a dip
    # means the vocabulary changed — likely a new beat.
    sims: list[float] = []
    for i in range(len(blocks) - 1):
        left = _merge(blocks, max(0, i - _WINDOW + 1), i + 1)
        right = _merge(blocks, i + 1, min(len(blocks), i + 1 + _WINDOW))
        sims.append(_cosine(left, right))

    depths = _depth_scores(sims)
    for i in range(len(blocks) - 1):
        gap = blocks[i + 1][0] - blocks[i][1]
        if gap >= _GAP_BONUS_SECS:
            depths[i] += 0.25  # silence corroborates the lexical dip

    mean = sum(depths) / len(depths)
    std = math.sqrt(sum((d - mean) ** 2 for d in depths) / len(depths))
    threshold = mean + 0.5 * std
    # Span, not absolute end time: on a chapter window an hour into the
    # episode, ``duration`` is the absolute clock and would inflate min_len.
    min_len = max(45.0, (duration - transcript.cues[0].start) / 60)

    candidates = sorted(
        (i for i, d in enumerate(depths) if d > threshold),
        key=lambda i: depths[i], reverse=True,
    )
    cuts: list[float] = []
    for i in candidates:
        t = blocks[i + 1][0]
        if all(abs(t - c) >= min_len for c in cuts) \
                and t - transcript.cues[0].start >= min_len \
                and duration - t >= min_len:
            cuts.append(t)
    cuts.sort()

    starts = [transcript.cues[0].start, *cuts]
    segs = [
        _titled(Segment(start=s, end=e), transcript)
        for s, e in zip(starts, [*cuts, duration])
    ]
    return segs


def _blocks(transcript: Transcript) -> list[tuple[float, float, Counter]]:
    """Pool cues into ~fixed-length blocks of (start, end, token counts)."""
    from .briefing import _tokens

    out: list[tuple[float, float, Counter]] = []
    tokens: Counter = Counter()
    b_start: Optional[float] = None
    b_end = 0.0
    for cue in transcript.cues:
        if b_start is None:
            b_start = cue.start
        tokens.update(_tokens(cue.text))
        b_end = cue.end if cue.end is not None else cue.start
        if b_end - b_start >= _BLOCK_SECS:
            out.append((b_start, b_end, tokens))
            tokens, b_start = Counter(), None
    if b_start is not None and tokens:
        out.append((b_start, b_end, tokens))
    return out


def _merge(blocks: list, lo: int, hi: int) -> Counter:
    merged: Counter = Counter()
    for _, _, tokens in blocks[lo:hi]:
        merged.update(tokens)
    return merged


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    num = sum(v * b.get(t, 0) for t, v in a.items())
    den = math.sqrt(sum(v * v for v in a.values())) \
        * math.sqrt(sum(v * v for v in b.values()))
    return num / den if den else 0.0


def _depth_scores(sims: list[float]) -> list[float]:
    """TextTiling depth: how far each similarity dips below its neighbouring
    peaks on both sides. Deep valleys = strong topic shifts."""
    depths = []
    for i, s in enumerate(sims):
        lpeak, j = s, i - 1
        while j >= 0 and sims[j] >= lpeak:
            lpeak, j = sims[j], j - 1
        rpeak, j = s, i + 1
        while j < len(sims) and sims[j] >= rpeak:
            rpeak, j = sims[j], j + 1
        depths.append((lpeak - s) + (rpeak - s))
    return depths


def _titled(seg: Segment, transcript: Transcript) -> Segment:
    """Label a topic segment with its most salient line (same trick as the
    TOC), truncated to something scannable."""
    from .briefing import _term_weights, _tokens

    window = transcript.window(seg.start, seg.end if seg.end is not None
                               else transcript.duration)
    if window:
        weights = _term_weights(transcript)
        best = max(window, key=lambda c: sum(
            weights.get(t, 0.0) for t in _tokens(c.text)))
        text = best.text.strip()
        seg.title = text[:97] + "…" if len(text) > 100 else text
    return seg
