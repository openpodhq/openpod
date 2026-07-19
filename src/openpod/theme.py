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

Encoding is a separate ladder from color, and stripping ANSI does not help:
``▸``, the box rules, ``·`` and ``—`` are still non-ASCII. On a stdout that
cannot encode them (a Windows redirect falling back to cp1252, a POSIX/C
locale in CI) an unguarded ``print`` raises ``UnicodeEncodeError`` and takes
the command down. :func:`enable_safe_output` installs an error handler that
transliterates to ASCII instead — every mapping is 1:1, so the banner box
stays aligned.
"""

from __future__ import annotations

import codecs
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
    return _vt_capable()


_vt_state: Optional[bool] = None


def _vt_capable() -> bool:
    """ANSI is only safe once the console actually interprets it. Always
    true on a POSIX TTY; on Windows the legacy console prints raw escapes
    unless VT processing is switched on, so probe (and enable) it once."""
    global _vt_state
    if _vt_state is None:
        _vt_state = sys.platform != "win32" or _enable_windows_vt()
    return _vt_state


def _enable_windows_vt() -> bool:  # pragma: no cover - Windows only
    # VT-native hosts announce themselves; no console-mode surgery needed.
    if (os.environ.get("WT_SESSION")            # Windows Terminal
            or os.environ.get("ConEmuANSI") == "ON"
            or os.environ.get("ANSICON")
            or os.environ.get("TERM")):         # mintty / msys / cygwin
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        vt_flag = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if mode.value & vt_flag:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | vt_flag))
    except Exception:
        return False


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
    if sys.platform == "win32":
        # No COLORTERM/TERM convention on Windows; the _style_enabled gate
        # already proved VT works, and VT consoles there are 24-bit.
        return "truecolor"
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


# --- encoding safety -------------------------------------------------------
# Non-ASCII lives in ~38 modules (— · … → ↳ ✓ ✗ ═ ‖ §). Per-module ASCII
# constants would be endless and would drift; one codec error handler covers
# every print in the package, including ones not written yet.
_ASCII_FALLBACKS = {
    "▸": ">", "✓": "+", "✗": "x",          # signature + status glyphs
    "┌": "+", "┐": "+", "└": "+", "┘": "+",  # banner box corners
    "─": "-", "│": "|", "═": "=",            # rules
    "·": "-", "—": "-", "–": "-", "±": "+",  # punctuation
    "→": ">", "←": "<", "↔": "-", "↳": ">",  # arrows
    "‖": "|", "§": "S", "“": '"', "”": '"',
    "…": ".", "é": "e", "ü": "u",
}


def _ascii_error_handler(exc: UnicodeError) -> tuple[str, int]:
    """Map an unencodable run to ASCII. 1:1 so column alignment survives."""
    bad = exc.object[exc.start:exc.end]  # type: ignore[attr-defined]
    return ("".join(_ASCII_FALLBACKS.get(ch, "?") for ch in bad), exc.end)  # type: ignore[attr-defined]


codecs.register_error("openpod_ascii", _ascii_error_handler)


def enable_safe_output() -> None:
    """Guard stdout/stderr against ``UnicodeEncodeError``.

    A no-op on the UTF-8 terminals almost everyone has; only rewires a stream
    that genuinely cannot carry the glyph.
    """
    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", None)
        if not encoding:
            continue
        try:
            GLYPH.encode(encoding)
        except (UnicodeEncodeError, LookupError):
            try:
                stream.reconfigure(errors="openpod_ascii")  # type: ignore[union-attr]
            except (AttributeError, OSError, ValueError):
                pass  # nothing more we can safely do; never block the command
