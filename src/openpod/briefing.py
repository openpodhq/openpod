"""Deterministic, local extraction of key ideas + a navigable TOC.

OpenPod is *agent-native*: the user's own AI writes the final personalized,
cited briefing. This module does the cheap, local, deterministic half — pick
salient moments, group them into a table-of-contents, attach deep-links — and
lays down a ``briefing.md`` scaffold the agent fills in. No network, no LLM.

The scoring here is intentionally simple (TF salience + position + length). The
context-aware "which moment matters to *this* user" model is the product's real
differentiator and lives outside this baseline.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Optional

from . import theme
from .deeplink import build_deeplink
from .models import Idea, SourceRef, Transcript, format_timestamp

# Design D7: one small attribution line at the foot of generated
# briefing.md / ideas.md — never above user content. ?ref=briefing is a
# measurement channel (Marketing Plan §7); don't drop it.
_ATTRIBUTION = "— cited with [OpenPod](https://github.com/openpod/openpod?ref=briefing)"

# A compact English stopword set — enough to stop function words dominating TF.
_STOP = set(
    "the a an and or but if then of to in on at for with by from as is are was "
    "were be been being it its this that these those i you he she we they them "
    "his her their our your my me him us so not no do does did have has had will "
    "would can could should may might must just about into over under out up down "
    "what which who whom whose when where why how all any both each few more most "
    "other some such than too very s t don now yeah like know think really going "
    "gonna kind sort thing things stuff okay right".split()
)
_WORD = re.compile(r"[a-z][a-z'\-]+")


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2]


def _term_weights(transcript: Transcript) -> dict[str, float]:
    """Log-scaled term frequencies over the whole transcript."""
    counts: Counter[str] = Counter()
    for cue in transcript:
        counts.update(_tokens(cue.text))
    return {term: 1.0 + math.log(n) for term, n in counts.items()}


def extract_ideas(transcript: Transcript, source: Optional[SourceRef] = None,
                  *, k: int = 8, min_gap: float = 45.0) -> list[Idea]:
    """Return up to ``k`` salient, time-spread ideas with deep-links.

    ``min_gap`` enforces spacing (seconds) so picks span the episode rather than
    clustering in one dense stretch.
    """
    if not len(transcript):
        return []

    weights = _term_weights(transcript)
    n = len(transcript.cues)
    scored: list[tuple[float, int]] = []
    for i, cue in enumerate(transcript.cues):
        toks = _tokens(cue.text)
        if not toks:
            continue
        salience = sum(weights.get(t, 0.0) for t in toks) / math.sqrt(len(toks))
        # Mild boost for the opening (framing) and a length sweet-spot.
        position = 1.12 if i < n * 0.08 else 1.0
        length = min(len(toks) / 12.0, 1.0)
        scored.append((salience * position * length, i))

    scored.sort(reverse=True)

    picked: list[int] = []
    for _, i in scored:
        start = transcript.cues[i].start
        if all(abs(start - transcript.cues[j].start) >= min_gap for j in picked):
            picked.append(i)
        if len(picked) >= k:
            break
    picked.sort()

    ideas: list[Idea] = []
    for i in picked:
        cue = transcript.cues[i]
        text = _expand(transcript, i)
        deeplink = build_deeplink(source, cue.start) if source else None
        ideas.append(Idea(text=text, start=cue.start, end=cue.end,
                          deeplink=deeplink, score=round(_score_of(scored, i), 3)))
    return ideas


def _expand(transcript: Transcript, i: int, *, max_chars: int = 240) -> str:
    """Grow a pick into a readable sentence-ish span by absorbing the next cue(s)."""
    parts = [transcript.cues[i].text]
    j = i + 1
    while j < len(transcript.cues) and sum(len(p) for p in parts) < max_chars:
        nxt = transcript.cues[j].text
        parts.append(nxt)
        if re.search(r"[.!?]\s*$", nxt):
            break
        j += 1
    return " ".join(parts).strip()


def _score_of(scored: list[tuple[float, int]], i: int) -> float:
    for s, idx in scored:
        if idx == i:
            return s
    return 0.0


# --------------------------------------------------------------------------- #
# Table of contents (navigable structure — the "CandleKeep lesson")
# --------------------------------------------------------------------------- #


def build_toc(transcript: Transcript, source: Optional[SourceRef] = None,
              *, segments: int = 8, structure: Optional[list] = None) -> list[Idea]:
    """A navigable TOC. When real ``structure`` is available (creator chapters
    or detected topic segments — see :mod:`openpod.segments`), each TOC entry
    is one of those beats; otherwise fall back to ~equal time slices labeled
    with their most salient line."""
    if not len(transcript):
        return []
    if structure and (structure[0].origin == "chapters" or len(structure) >= 3):
        return [
            Idea(text=seg.title or "(untitled segment)", start=seg.start,
                 end=seg.end,
                 deeplink=build_deeplink(source, seg.start) if source else None)
            for seg in structure
        ]
    duration = transcript.duration or transcript.cues[-1].start
    if duration <= 0:
        return []

    weights = _term_weights(transcript)
    seg_len = duration / segments
    toc: list[Idea] = []
    for s in range(segments):
        lo, hi = s * seg_len, (s + 1) * seg_len
        window = [c for c in transcript.cues if lo <= c.start < hi]
        if not window:
            continue
        best = max(
            window,
            key=lambda c: sum(weights.get(t, 0.0) for t in _tokens(c.text)),
        )
        deeplink = build_deeplink(source, window[0].start) if source else None
        toc.append(
            Idea(text=best.text.strip(), start=window[0].start,
                 end=window[-1].end, deeplink=deeplink)
        )
    return toc


# --------------------------------------------------------------------------- #
# Markdown rendering
# --------------------------------------------------------------------------- #


def _stamp(seconds: float, link: Optional[str]) -> str:
    return theme.moment_markdown(seconds, link)


def ideas_markdown(ideas: list[Idea], source: Optional[SourceRef] = None) -> str:
    """Render each idea with its full anchor ladder, labeled: the **beat**
    (bold, primary — where the idea starts being articulated), the creator's
    *chapter* when distinct, and the exact *said-at* moment. Cheap to give all
    three; the reader picks the landing."""
    title = (source.title if source else None) or "Key ideas"
    lines = [f"# Key ideas — {title}", ""]
    if any(i.segment_start is not None for i in ideas):
        lines += ["_Links: **bold** jumps to the start of the beat; "
                  "“chapter” to the creator's chapter; “said” to the exact "
                  "sentence._", ""]
    for idea in ideas:
        moment = _stamp(idea.start, idea.deeplink)
        trail = []
        if idea.chapter_start is not None:
            name = f" “{idea.chapter_title}”" if idea.chapter_title else ""
            trail.append(f"chapter{name} {_stamp(idea.chapter_start, idea.chapter_deeplink)}")
        if idea.segment_start is not None and idea.segment_start < idea.start:
            primary = _stamp(idea.segment_start, idea.segment_deeplink)
            trail.append(f"said {moment}")
        else:
            primary = moment
        suffix = f" _({' · '.join(trail)})_" if trail else ""
        lines.append(f"- **{primary}** — {idea.text}{suffix}")
    lines += ["", "---", _ATTRIBUTION, ""]
    return "\n".join(lines)


def briefing_scaffold(source: SourceRef, transcript: Transcript,
                      toc: list[Idea], *, persona: Optional[str] = None) -> str:
    """A briefing.md template for the agent to complete.

    We fill in the deterministic parts (metadata, navigable TOC, coverage note)
    and leave the *personalized* prose and citations to the user's agent.
    """
    title = source.title or "Untitled episode"
    show = f"\n**Show:** {source.show}" if source.show else ""
    dur = format_timestamp(transcript.duration)
    persona_note = (
        "\n\n> _Personalize the summary below against the reader's `persona.md`._"
        if persona is None
        else f"\n\n> _Reader context loaded from persona.md ({len(persona)} chars)._"
    )

    toc_lines = []
    for item in toc:
        stamp = theme.moment_markdown(item.start, item.deeplink)
        toc_lines.append(f"1. {stamp} — {item.text}")

    return (
        f"# Briefing — {title}{show}\n"
        f"**Length:** {dur} · **Transcript source:** {transcript.source}"
        f"{persona_note}\n\n"
        "## Triage\n"
        "<!-- agent: skip / the N minutes that matter / listen fully — with jump-links -->\n\n"
        "## Summary\n"
        "<!-- agent: personalized, cited summary; every claim links to a timestamp above -->\n\n"
        "## Navigate\n" + "\n".join(toc_lines) + "\n\n"
        "---\n"
        "<sub>Scaffold generated by OpenPod. The summary is authored by your agent.</sub>\n\n"
        f"{_ATTRIBUTION}\n"
    )
