"""Burned captions as ASS — the per-word capability SRT cannot express.

The old burn path handed libass one uniform ``force_style`` per line: color,
outline, box, position — applied to the whole line at once. Every named
caption style (keyword / marker / karaoke) hinges on the one thing that
model cannot say: *a differently-colored word*. ASS inline override tags
carry it, so the burn now writes an ``.ass`` next to the derivative and
styles live in the file, not in a filter argument.

Conventions (agent-annotated, never guessed — the ‖ contract extended):

- ``*word*`` (or ``*two words*``) marks THE keyword of a line. The agent
  chooses it per line — for Hebrew and any RTL text this is the only honest
  option; no heuristic keyword list survives translation.
- ``‖`` (captions.FORCE_BREAK) inside a burned line becomes a hard line
  break, exactly what the translation flow uses it to mean.

Styles:

- ``plain``   — one uniform style (the pre-ASS look, byte-for-byte intent).
- ``keyword`` — the agent-marked word renders in ``keyword_color``;
  typically ``boxed: false`` with weight + shadow (mockup 1a).
- ``marker``  — same keyword rendering; typically ``boxed: true``
  (mockup 1c). The name records intent; the box comes from
  ``caption_style.boxed`` either way.
- ``karaoke`` — words light up in ``keyword_color`` as they are spoken,
  consuming the clip's word-level track (``word_level=True`` →
  ``words.json``). Without word timings it degrades to ``keyword``.
"""

from __future__ import annotations

import re
from typing import Optional

from .captions import FORCE_BREAK
from .theme import BLUE_DARK

STYLES = ("plain", "keyword", "marker", "karaoke")

# *word* / *two words* — the agent's per-line keyword mark.
_KEYWORD_RX = re.compile(r"\*([^*\n]+?)\*")

# ffmpeg's own SRT→ASS defaults; keeping them keeps the plain burn looking
# exactly as it did under force_style.
_PLAY_RES = (384, 288)
_FONT_SIZE = 16
_MARGIN_V = 40


def parse_keywords(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Strip ``*…*`` marks, returning clean text + keyword spans as
    ``(start, end)`` character offsets into the clean text."""
    spans: list[tuple[int, int]] = []
    out: list[str] = []
    pos = 0
    clean_len = 0
    for m in _KEYWORD_RX.finditer(text):
        out.append(text[pos:m.start()])
        clean_len += m.start() - pos
        word = m.group(1)
        spans.append((clean_len, clean_len + len(word)))
        out.append(word)
        clean_len += len(word)
        pos = m.end()
    out.append(text[pos:])
    return "".join(out), spans


def hex_to_ass(hex_color: str, alpha: str = "00") -> str:
    """``#RRGGBB`` -> libass ``&HAABBGGRR`` (note the reversed byte order)."""
    h = (hex_color or "#FFFFFF").lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha}{b}{g}{r}"


def _ts(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _esc(text: str) -> str:
    return text.replace("{", r"\{").replace("}", r"\}")


def _line_words(words: list[dict], start: float, end: float) -> list[dict]:
    """Words whose midpoint falls inside the line's window."""
    out = []
    for w in words:
        mid = (w["start"] + w.get("end", w["start"])) / 2
        if start - 0.05 <= mid < end + 0.05:
            out.append(w)
    return out


def build_ass(lines: list[tuple[float, float, str]], *, style: dict,
              mode: str = "plain",
              words: Optional[list[dict]] = None) -> str:
    """Render caption lines to a complete ASS document.

    ``lines`` are clip-relative ``(start, end, text)``; ``words`` is the
    clip-relative word-level track (karaoke only). Deterministic: same
    input, same bytes.
    """
    if mode not in STYLES:
        raise ValueError(f"caption style must be one of {STYLES}, got {mode!r}")
    if mode == "karaoke" and not words:
        mode = "keyword"   # honest degradation; the caller notes it

    boxed = style.get("boxed", True)
    primary_hex = style.get("color", "#FFFFFF")
    keyword_hex = style.get("keyword_color") or BLUE_DARK
    outline_hex = style.get("outline", "#000000")
    weight = style.get("weight")
    bold = -1 if (weight is not None and int(weight) >= 600) else 0
    shadow = int(style.get("shadow") or 0)

    primary = hex_to_ass(primary_hex)
    keyword = hex_to_ass(keyword_hex)
    outline = hex_to_ass(outline_hex, alpha="60" if boxed else "00")
    back = hex_to_ass(outline_hex, alpha="60" if boxed else "80")
    # Karaoke fill: unsung shows SecondaryColour, sung becomes PrimaryColour.
    style_primary = keyword if mode == "karaoke" else primary
    secondary = primary if mode == "karaoke" else keyword
    border_style = 4 if boxed else 1
    alignment = 8 if style.get("position") == "top" else 2
    font = style.get("font") or "Arial"

    L = [
        "[Script Info]",
        "; Generated by openpod — style lives here, not in a filter argument.",
        "ScriptType: v4.00+",
        f"PlayResX: {_PLAY_RES[0]}",
        f"PlayResY: {_PLAY_RES[1]}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: OpenPod,{font},{_FONT_SIZE},{style_primary},{secondary},"
        f"{outline},{back},{bold},0,0,0,100,100,0,0,{border_style},1,{shadow},"
        f"{alignment},10,10,{_MARGIN_V},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text",
    ]

    for start, end, raw in lines:
        clean, spans = parse_keywords(raw)
        if mode == "karaoke":
            text = _karaoke_text(clean, start, end,
                                 _line_words(words or [], start, end),
                                 primary=primary)
        else:
            body = clean.replace("\n", r"\N").replace(FORCE_BREAK + " ", FORCE_BREAK)
            body = body.replace(FORCE_BREAK, r"\N")
            if mode in ("keyword", "marker") and spans:
                text = _highlight(clean, spans, keyword=keyword,
                                  primary=primary)
            else:
                text = _esc(body)
        L.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},OpenPod,,0,0,0,,{text}")

    return "\n".join(L) + "\n"


def _highlight(clean: str, spans: list[tuple[int, int]], *,
               keyword: str, primary: str) -> str:
    """Wrap keyword spans in inline color overrides."""
    out: list[str] = []
    pos = 0
    for s, e in spans:
        out.append(_esc(clean[pos:s]))
        out.append("{\\c" + keyword + "}" + _esc(clean[s:e])
                   + "{\\c" + primary + "}")
        pos = e
    out.append(_esc(clean[pos:]))
    text = "".join(out)
    return text.replace(FORCE_BREAK + " ", FORCE_BREAK).replace(FORCE_BREAK, r"\N")


def _karaoke_text(clean: str, start: float, end: float,
                  line_words: list[dict], *, primary: str) -> str:
    """``{\\k}``-timed text; lines the word track doesn't cover render
    uniformly in the base color (the style's Primary is the sung color, so
    uncovered lines override back to it)."""
    clean = clean.replace(FORCE_BREAK, " ").strip()
    if not line_words:
        return "{\\c" + primary + "}" + _esc(clean)
    parts: list[str] = []
    cursor = start
    for w in line_words:
        gap_cs = int(round((w["start"] - cursor) * 100))
        if gap_cs > 0:
            parts.append("{\\k" + str(gap_cs) + "}")
        w_end = w.get("end", w["start"])
        dur_cs = max(1, int(round((w_end - w["start"]) * 100)))
        word_clean, _ = parse_keywords(w.get("text", ""))
        parts.append("{\\k" + str(dur_cs) + "}" + _esc(word_clean) + " ")
        cursor = w_end
    return "".join(parts).rstrip()
