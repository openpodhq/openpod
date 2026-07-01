"""Build "jump to the moment" deep-links.

The rule from the Tooling Reference:

* YouTube  -> ``watch?v=ID&t=754s``
* Spotify  -> ``open.spotify.com/episode/{id}?t=754`` (after id resolution)
* Podcasts -> the open enclosure with a media-fragment ``#t=754`` (playable by
  any HTML5 player / our own thin player)
* Apple / Pocket Casts / Overcast -> no public ``?t=`` URL; return ``None`` and
  let the caller fall back to an exported timestamp.

We *navigate*, we never re-host. That is the legal thesis.
"""

from __future__ import annotations

from typing import Optional

from .models import SourceRef


def build_deeplink(source: SourceRef, seconds: float) -> Optional[str]:
    """Return a deep-link to ``seconds`` into ``source``, or ``None`` if the
    platform exposes no public timestamped URL."""
    t = max(0, int(round(seconds)))

    if source.kind == "youtube" and source.video_id:
        return f"https://www.youtube.com/watch?v={source.video_id}&t={t}s"

    if source.kind == "spotify" and source.episode_id:
        return f"https://open.spotify.com/episode/{source.episode_id}?t={t}"

    if source.kind == "podcast" and source.audio_url:
        # Media Fragments URI — works in our own thin player and most browsers.
        sep = "&" if "#" in source.audio_url else "#"
        return f"{source.audio_url}{sep}t={t}"

    # file / unknown / Apple etc. — no navigable public link.
    return None


def deeplink_card(source: SourceRef, seconds: float, quote: str) -> str:
    """A small shareable markdown card: quote + cited source + open-at link.

    This is the *deep-link card* growth atom — it distributes a link, not the
    content. Rendered as plain markdown so it works anywhere.
    """
    from .models import format_timestamp

    link = build_deeplink(source, seconds)
    ts = format_timestamp(seconds)
    title = source.title or "this episode"
    show = f" — {source.show}" if source.show else ""
    open_at = f"[▶ open at {ts}]({link})" if link else f"open at {ts}"

    return (
        f"> {quote.strip()}\n\n"
        f"*{title}{show}* · {open_at}\n\n"
        f"<sub>made with OpenPod</sub>"
    )
