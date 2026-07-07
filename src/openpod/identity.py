"""Cross-codebase episode identity — the bridge to the player/server (§6.5).

Every episode the player and sync server key is stamped with an ``episode_key``.
For OpenPod's push/pull to line up with the player's records **byte-for-byte**,
this module ports the player's ``schemas/src/identity.ts`` exactly:

* ``normalize_feed_url`` — lowercase scheme+host, strip the default port, strip a
  trailing path slash, drop ``utm_*`` query params (case-insensitive) while
  keeping the rest in original order. Redirects are **never** resolved.
* ``episode_key`` — the fallback ladder:
    1. ``sha256(normalized + "\\n" + guid)``            → confidence ``"guid"``
    2. ``sha256(normalized + "\\n" + enclosure_url)``    → confidence ``"enclosure"``
    3. ``sha256(normalized + title + published)``        → confidence ``"derived"``

  Note: the ``"\\n"`` separator is present for the guid/enclosure rungs but
  **absent** for the derived rung — this matches the TS source and the shared
  test vectors, so keep it as-is.

Standard library only (``hashlib`` + ``urllib``); safe to import anywhere.
"""

from __future__ import annotations

import hashlib
from typing import Optional
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

KeyConfidence = str  # "guid" | "enclosure" | "derived"

# The WHATWG "path percent-encode" set leaves these ASCII chars alone; quote()
# already spares letters/digits and -._~, so we only extend `safe`. Everything
# else (space, non-ASCII bytes, " < > ` { } …) is percent-encoded — matching
# how the browser's `new URL().pathname` normalizes, which the player uses.
_PATH_SAFE = "/:@!$&'()*+,;=~-._"


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_host(host: str) -> str:
    """Punycode an internationalized host, matching the WHATWG URL parser.

    ``new URL('https://münchen.example.com').hostname`` is ``xn--mnchen-3ya…``;
    a raw-unicode host here would derive a different episode_key than the player
    for every IDN feed. stdlib IDNA covers the common (café/münchen/CJK) cases;
    a host that fails to encode falls back to its raw lowercase form.
    """
    if host.isascii():
        return host
    try:
        return host.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return host


def normalize_feed_url(raw: str) -> str:
    """Canonicalize a feed URL the same way the player does.

    Mirrors ``normalizeFeedUrl`` in the TS ``schemas`` package. Redirects are
    deliberately *not* followed — two clients that saw the same feed URL must
    derive the same key without any network round-trip.
    """
    u = urlsplit(raw.strip())
    scheme = u.scheme.lower()
    host = _normalize_host((u.hostname or "").lower())

    # Strip the default port for the scheme; keep any other explicit port.
    port = u.port
    is_default = (
        port is None
        or (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    )
    if not is_default:
        host = f"{host}:{port}"

    # Percent-encode the path like the browser URL parser (non-ASCII, space,
    # etc.), then strip a trailing slash — matching the player byte-for-byte.
    path = quote(u.path, safe=_PATH_SAFE)
    if path.endswith("/"):
        path = path[:-1]

    # Keep non-utm_ params in original order (parse_qsl preserves order and
    # blank values; urlencode re-serializes exactly like URLSearchParams).
    kept = [
        (k, v)
        for k, v in parse_qsl(u.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
    ]
    query = urlencode(kept)

    return f"{scheme}://{host}{path}" + (f"?{query}" if query else "")


def episode_key(
    feed_url: str,
    guid: Optional[str] = None,
    audio_url: Optional[str] = None,
    title: Optional[str] = None,
    published: Optional[str] = None,
) -> tuple[str, KeyConfidence]:
    """Return ``(episode_key, confidence)`` for an episode.

    Ports ``deriveEpisodeKey`` from the TS ``schemas`` package. ``audio_url`` is
    the enclosure URL. A :class:`~openpod.models.SourceRef` maps as
    ``feed_url = source.url``, ``guid = source.guid``,
    ``audio_url = source.audio_url``.
    """
    normalized = normalize_feed_url(feed_url)
    if guid and guid.strip():
        return _sha256_hex(f"{normalized}\n{guid.strip()}"), "guid"
    if audio_url and audio_url.strip():
        return _sha256_hex(f"{normalized}\n{audio_url.strip()}"), "enclosure"
    # Derived rung: NO separator between the three parts (matches TS).
    return _sha256_hex(f"{normalized}{title or ''}{published or ''}"), "derived"


def source_episode_key(source) -> tuple[str, KeyConfidence]:
    """Convenience: derive the key from a :class:`~openpod.models.SourceRef`.

    Returns ``("", "")`` if the source has no feed URL to key on.
    """
    if source is None or not getattr(source, "url", None):
        return "", ""
    return episode_key(
        source.url,
        guid=source.guid,
        audio_url=source.audio_url,
        title=source.title,
        published=source.published,
    )
