"""The OpenPod brand, expressed in text.

Black, white, one blue — the blue only ever marks *the moment* (timestamps,
deep-links, the jump affordance, the CTA). Nowhere else. Success/failure read
from the ``✓``/``✗`` glyph and weight, never color.

This is the only module in the package that defines colors or glyphs; every
other module renders through the functions here. Colors come from
``brand/brand-tokens.json`` (the single source of truth) — never hand-copy a
hex value into code.

Color capability ladder, checked in order:
    truecolor (``COLORTERM=truecolor|24bit``)
    -> 256-color (``TERM`` contains ``256color``)
    -> no color.
Force-disabled by ``NO_COLOR`` (any value), a non-TTY ``stdout``, or
``OPENPOD_NO_COLOR=1`` — in all three cases every function here returns
plain text with zero escape codes, so the brand still reads in plaintext.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

_BRAND_DIR = Path(__file__).parent / "brand"
_TOKENS = json.loads((_BRAND_DIR / "brand-tokens.json").read_text(encoding="utf-8"))
_BANNER_TEMPLATE = (_BRAND_DIR / "banner.txt").read_text(encoding="utf-8").rstrip("\n")

GLYPH = _TOKENS["signature"]["glyph"]  # ▸  U+25B8

# Hex values for non-terminal surfaces (e.g. the deep-link card, a fixed
# light-mode artifact) — derived from the tokens, never hand-copied.
INK = _TOKENS["color"]["ink"]["hex"]
PAPER = _TOKENS["color"]["paper"]["hex"]
BLUE_LIGHT = _TOKENS["color"]["blue_light"]["hex"]
BLUE_DARK = _TOKENS["color"]["blue_dark"]["hex"]

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"

_OK_GLYPH = "✓"   # ✓
_FAIL_GLYPH = "✗"  # ✗


def _style_enabled() -> bool:
    """Purity gate shared by every function: color, bold, dim, and OSC 8
    hyperlinks all live or die together, so piped/``NO_COLOR`` output is
    byte-identical and carries zero escape codes."""
    if "NO_COLOR" in os.environ:
        return False
    if os.environ.get("OPENPOD_NO_COLOR") == "1":
        return False
    if not sys.stdout.isatty():
        return False
    return True


def _theme_variant() -> str:
    override = os.environ.get("OPENPOD_THEME", "").strip().lower()
    if override in ("light", "dark"):
        return override
    return "dark"  # our audience overwhelmingly runs dark terminals


def _color_tier() -> str:
    """``'truecolor'`` | ``'256'`` | ``'none'``."""
    if not _style_enabled():
        return "none"
    if os.environ.get("COLORTERM") in ("truecolor", "24bit"):
        return "truecolor"
    if "256color" in os.environ.get("TERM", ""):
        return "256"
    return "none"


def _blue_code() -> str:
    tier = _color_tier()
    if tier == "none":
        return ""
    variant = _theme_variant()
    token = _TOKENS["color"]["blue_dark" if variant == "dark" else "blue_light"]
    if tier == "truecolor":
        return f"\033[{token['ansi_24bit']}m"
    return f"\033[38;5;{token['ansi_256']}m"


def _format_seconds(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def moment_plain(seconds: float) -> str:
    """The signature with no styling at all: ``▸ m:ss``. For data payloads
    (JSON display fields) where escape codes and markdown have no place —
    agents paste this verbatim, which is how the glyph reaches chat panes."""
    return f"{GLYPH} {_format_seconds(seconds)}"


def moment_markdown(seconds: float, url: Optional[str] = None) -> str:
    """The signature for files: a markdown link when ``url`` is available
    (``[▸ 12:34](url)`` — the host renders the link in its own blue), else
    inline code (`` `▸ 12:34` ``) so the glyph still survives without a
    dead link. Every timestamp in a generated artifact goes through this."""
    text = moment_plain(seconds)
    return f"[{text}]({url})" if url else f"`{text}`"


def moment(seconds: float, url: Optional[str] = None) -> str:
    """The signature: ``▸ m:ss``, blue, wrapped in a clickable OSC 8
    hyperlink when ``url`` is given. Every timestamp the CLI prints goes
    through this function.

    Under no-color or a non-TTY: ``▸ 12:34 <url>`` — the glyph stays, the
    link becomes plain text beside it.
    """
    text = moment_plain(seconds)
    if not _style_enabled():
        return f"{text} {url}" if url else text
    blue = _blue_code()
    styled = f"{blue}{text}{_RESET}" if blue else text
    if url:
        return f"\033]8;;{url}\033\\{styled}\033]8;;\033\\"
    return styled


def path(text: str) -> str:
    """The artifact address a mutating command prints — bold, never blue."""
    if not _style_enabled():
        return text
    return f"{_BOLD}{text}{_RESET}"


def ok(text: str = "") -> str:
    """``✓`` + weight, no color."""
    line = f"{_OK_GLYPH} {text}".rstrip() if text else _OK_GLYPH
    if not _style_enabled():
        return line
    return f"{_BOLD}{_OK_GLYPH}{_RESET}{f' {text}' if text else ''}"


def fail(text: str = "") -> str:
    """``✗`` + weight, no color."""
    line = f"{_FAIL_GLYPH} {text}".rstrip() if text else _FAIL_GLYPH
    if not _style_enabled():
        return line
    return f"{_BOLD}{_FAIL_GLYPH}{_RESET}{f' {text}' if text else ''}"


def dim(text: str) -> str:
    if not _style_enabled():
        return text
    return f"{_DIM}{text}{_RESET}"


def banner() -> str:
    """Renders ``brand/banner.txt`` — the CLI start banner."""
    from . import __version__

    text = _BANNER_TEMPLATE.replace("v0.1", f"v{__version__}")
    if not _style_enabled():
        return text
    blue = _blue_code()
    lines = text.split("\n")
    styled = []
    for i, line in enumerate(lines):
        if i in (0, 2):  # box border
            styled.append(f"{blue}{line}{_RESET}" if blue else line)
        elif i == 1:  # glyph + wordmark line
            if "OpenPod" in line:
                before, _, after = line.partition("OpenPod")
                wordmark = f"{_BOLD}OpenPod{_RESET}{blue}" if blue else f"{_BOLD}OpenPod{_RESET}"
                styled.append(
                    f"{blue}{before}{_RESET}{wordmark}{after}{_RESET}" if blue
                    else f"{before}{wordmark}{after}"
                )
            else:
                styled.append(line)
        else:  # footer
            styled.append(f"{_DIM}{line}{_RESET}" if line.strip() else line)
    return "\n".join(styled)
