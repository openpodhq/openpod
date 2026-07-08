"""Cross-source timestamp alignment — probe-and-bisect.

A transcript fetched from the fastest source (usually YouTube captions)
doesn't necessarily line up with the same episode's audio on the platform
the user pasted: intros get trimmed, ads get inserted. The offset between
two versions is **piecewise-constant** — it jumps at each ad break and is
flat in between — so the whole map can be recovered with a handful of
cheap probes:

1. Probe ``offset(t)`` near the start and end (a probe = transcribe or
   fingerprint a ~15s window of the target audio and locate its text in the
   reference transcript).
2. Equal offsets → constant in between; done in two probes.
3. Different → binary-search the jump point and recurse. O(log n) probes
   per ad break, ~10–20 probes for a typical episode.

The map is computed once per (episode, source-pair) and cached on the
crosswalk record. When alignment can't be trusted end-to-end (Spotify
re-hosts audio and may inject per-listener ads — we can never fetch the
exact stream a user hears), callers keep the ``approximate`` precision flag;
alignment narrows the error, honesty stays.

The probe is injected as a callable so the algorithm is testable without
audio; :func:`asr_probe` builds the real one from ffmpeg + faster-whisper.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .models import Transcript

# offset(t) for a probe time t in the *target* audio; None = probe failed
# (silence, music, ad content not present in the reference transcript).
ProbeFn = Callable[[float], Optional[float]]

# Offsets closer than this are "the same" (caption timing jitter).
TOLERANCE = 3.0
# Don't bisect intervals shorter than this — pin the jump to the midpoint.
MIN_INTERVAL = 30.0


@dataclass
class OffsetMap:
    """Piecewise-constant mapping: target-audio time -> reference offset.

    ``points`` is ``[(start_t, offset), ...]`` sorted by ``start_t``; the
    offset applies from ``start_t`` until the next point. ``map_time(t)``
    answers "reference transcript time t lands where in the target audio?"
    via the inverse shift.
    """

    points: list = field(default_factory=list)   # [(t, offset), ...]
    probes_used: int = 0
    complete: bool = True    # False when some probes failed and gaps remain

    def offset_at(self, t: float) -> Optional[float]:
        chosen = None
        for start_t, off in self.points:
            if t >= start_t:
                chosen = off
            else:
                break
        return chosen

    def to_target(self, reference_seconds: float) -> Optional[float]:
        """Map a reference-transcript time onto the target audio."""
        # points are in target-time; invert by scanning segments.
        for start_t, off in reversed(self.points):
            if reference_seconds >= start_t + off:
                return reference_seconds - off
        if self.points:
            return reference_seconds - self.points[0][1]
        return None

    def to_list(self) -> list:
        return [[round(t, 3), round(o, 3)] for t, o in self.points]

    @classmethod
    def from_list(cls, data: list) -> "OffsetMap":
        return cls(points=[(float(t), float(o)) for t, o in data])


def compute_offset_map(probe: ProbeFn, duration: float, *,
                       tolerance: float = TOLERANCE,
                       min_interval: float = MIN_INTERVAL,
                       max_probes: int = 40) -> OffsetMap:
    """Recover the piecewise-constant offset map with probe-and-bisect."""
    state = {"probes": 0, "failed": 0}

    def _probe(t: float) -> Optional[float]:
        if state["probes"] >= max_probes:
            return None
        state["probes"] += 1
        off = probe(t)
        if off is None:
            state["failed"] += 1
        return off

    lo_t = min(30.0, duration * 0.02)
    hi_t = max(duration - 60.0, duration * 0.9)
    lo = _probe(lo_t)
    hi = _probe(hi_t)

    points: list[tuple[float, float]] = []
    complete = True

    if lo is None and hi is None:
        return OffsetMap(points=[], probes_used=state["probes"], complete=False)
    if lo is None:
        lo, complete = hi, False
    if hi is None:
        hi, complete = lo, False

    points.append((0.0, lo))

    def _bisect(t1: float, off1: float, t2: float, off2: float) -> None:
        nonlocal complete
        if abs(off1 - off2) <= tolerance:
            return
        if t2 - t1 <= min_interval:
            # Pin the jump at the interval midpoint — good to ±min_interval/2.
            points.append((round((t1 + t2) / 2, 3), off2))
            return
        mid = (t1 + t2) / 2
        off_mid = _probe(mid)
        if off_mid is None:
            # Can't see into this interval; place the jump at mid but flag it.
            complete = False
            points.append((round(mid, 3), off2))
            return
        _bisect(t1, off1, mid, off_mid)
        _bisect(mid, off_mid, t2, off2)

    _bisect(lo_t, lo, hi_t, hi)
    points.sort(key=lambda p: p[0])
    # Collapse consecutive points with ~equal offsets.
    deduped: list[tuple[float, float]] = []
    for t, off in points:
        if deduped and abs(deduped[-1][1] - off) <= tolerance:
            continue
        deduped.append((t, off))
    return OffsetMap(points=deduped, probes_used=state["probes"],
                     complete=complete)


# --------------------------------------------------------------------------- #
# The real probe: ffmpeg window -> ASR -> locate text in reference transcript
# --------------------------------------------------------------------------- #

_word_re = re.compile(r"[a-z0-9']+")


def _words(text: str) -> list[str]:
    return _word_re.findall(text.lower())


def locate_text(reference: Transcript, words: list[str], *,
                min_overlap: float = 0.6) -> Optional[float]:
    """Find where a probe's word sequence occurs in the reference transcript.

    Slides over cue windows comparing word overlap; returns the reference
    time where the sequence starts, or None when nothing matches well
    (music, an inserted ad, ASR noise).
    """
    if len(words) < 5:
        return None
    target = set(words)
    best_t, best_score = None, 0.0
    cues = reference.cues
    for i, cue in enumerate(cues):
        # A window of roughly the probe's own length: generous windows make
        # the *preceding* cue score as well as the right one.
        window_words: list[str] = []
        j = i
        while j < len(cues) and len(window_words) < len(words) + 3:
            window_words.extend(_words(cues[j].text))
            j += 1
        if not window_words:
            continue
        overlap = len(target & set(window_words[:len(words) + 3])) / len(target)
        if overlap > best_score:
            best_score, best_t = overlap, cue.start
    if best_score < min_overlap:
        return None
    return best_t


def asr_probe(target_media: str, reference: Transcript, *,
              window: float = 15.0, model: str = "base") -> ProbeFn:
    """Build a real probe: cut ``window`` seconds at t from ``target_media``
    (ffmpeg), transcribe it locally, and locate the words in ``reference``.

    ``offset = reference_time - t``: positive offset means the reference
    transcript runs *ahead* of the target audio at that point.
    """
    from .asr import transcribe

    def probe(t: float) -> Optional[float]:
        with tempfile.TemporaryDirectory(prefix="openpod-align-") as td:
            snippet = Path(td) / "probe.wav"
            cmd = ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-t", f"{window:.2f}",
                   "-i", target_media, "-ac", "1", "-ar", "16000",
                   str(snippet)]
            proc = subprocess.run(cmd, capture_output=True)
            if proc.returncode != 0 or not snippet.exists():
                return None
            try:
                heard = transcribe(str(snippet), model=model)
            except Exception:
                return None
        words = _words(heard.text())
        ref_t = locate_text(reference, words)
        if ref_t is None:
            return None
        return ref_t - t

    return probe


def align_entry(entry, target_media: str, *,
                source_kind: str = "youtube") -> Optional[OffsetMap]:
    """Align a caught entry's transcript against a target audio file and
    persist the map on the entry's crosswalk record (keyed by the
    transcript-source kind)."""
    transcript = entry.read_transcript()
    if transcript is None or not len(transcript):
        return None
    duration = transcript.duration
    probe = asr_probe(target_media, transcript)
    omap = compute_offset_map(probe, duration)

    meta = entry.read_meta()
    key = meta.get("episode_key")
    if key:
        from .config import Workspace
        from .crosswalk import Crosswalk

        cw = Crosswalk(entry.workspace)
        ident = cw.get(key)
        if ident is not None:
            ident.offset_maps[source_kind] = omap.to_list()
            cw.put(ident)
    return omap
