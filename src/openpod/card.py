"""The deep-link share card — the growth atom `clip` emits.

Renders ``card.html`` from the vendored template (``brand/card-template.html``
— exact 1200x630, self-contained, opens offline with zero network calls) and,
best-effort, a 2x ``card.png`` when a headless renderer is installed. Core
install stays light on purpose: PNG rendering lives behind the ``card-png``
extra, never a hard dependency on Chromium/Playwright.

The citation hierarchy is structural, not cosmetic: source attribution (show
+ episode) renders above and larger than the OpenPod mark. Don't "fix" that
to make the mark more prominent — it's a citation, not an ad.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Optional

from . import theme
from .deeplink import build_deeplink
from .models import SourceRef, format_timestamp

_TEMPLATE = (Path(__file__).parent / "brand" / "card-template.html").read_text(encoding="utf-8")
_TOKEN_RE = re.compile(r"\{\{(\w+)\}\}")

# The k-proxy for kill-question R2 (Marketing Plan) — non-negotiable.
BACKLINK = "https://github.com/openpodhq/openpod?ref=card"


def _fill(template: str, **values: str) -> str:
    """Single-pass token substitution — a substituted value is never
    re-scanned for further ``{{TOKEN}}`` matches, so pathological quote text
    can't corrupt later substitutions."""
    return _TOKEN_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def render_card_html(source: Optional[SourceRef], seconds: float, quote: str,
                     deeplink: Optional[str] = None) -> str:
    """Render the 1200x630 deep-link card as a self-contained HTML string."""
    link = deeplink if deeplink is not None else (
        build_deeplink(source, seconds) if source else None
    )
    show = html.escape((source.show or "").strip()) if source and source.show else "OpenPod"
    episode = html.escape((source.title or "").strip()) if source and source.title else "this episode"
    quote_html = html.escape(quote.strip())
    ts = format_timestamp(seconds)
    badge = html.escape(f"{theme.GLYPH} {ts}")

    if link:
        moment_block = (
            f'<a href="{html.escape(link)}" style="display:flex; align-items:center; '
            f'gap:18px; text-decoration:none;">'
            f'<span style="font-size:34px; font-weight:600; color:{theme.BLUE_LIGHT}; '
            f'letter-spacing:0.01em;">{badge}</span>'
            f'<span style="font-size:16px; color:{theme.BLUE_LIGHT}; text-decoration:underline; '
            f'text-underline-offset:3px;">open at {html.escape(ts)} in the episode</span></a>'
        )
    else:
        moment_block = (
            f'<div style="display:flex; align-items:center;">'
            f'<span style="font-size:34px; font-weight:600; color:{theme.BLUE_LIGHT}; '
            f'letter-spacing:0.01em;">{badge}</span></div>'
        )

    return _fill(
        _TEMPLATE,
        SHOW=show, EPISODE=episode, QUOTE=quote_html, MOMENT_BLOCK=moment_block,
        BACKLINK=BACKLINK, PAPER=theme.PAPER, INK=theme.INK,
    )


def render_card_png(html_path: Path, out_path: Path, *, scale: int = 2) -> bool:
    """Best-effort 2x raster of ``html_path`` via a headless renderer.

    Returns ``True`` on success, ``False`` whenever a renderer isn't
    available or it fails for any reason — this must never block or crash
    the core `clip` flow. Requires the ``card-png`` extra
    (``pip install 'openpod[card-png]'``).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(
                    viewport={"width": 1200, "height": 630}, device_scale_factor=scale,
                )
                page.goto(html_path.resolve().as_uri())
                page.screenshot(path=str(out_path))
            finally:
                browser.close()
        return True
    except Exception:
        return False
