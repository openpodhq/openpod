"""Spotify ingestion — deterministic endpoints, not page scraping.

Spotify exposes no transcript and no public metadata API without OAuth, but
two keyless endpoints make resolution deterministic enough to stop scraping
119KB HTML pages and running open-ended web searches:

1. **oEmbed** (``open.spotify.com/oembed?url=...``) — the episode title,
   from a stable, documented endpoint.
2. **iTunes Search** (``itunes.apple.com/search?entity=podcastEpisode``) —
   keyless full-text episode search across the open podcast catalog; a hit
   carries ``feedUrl`` + ``episodeGuid``, i.e. the canonical RSS identity.

The match is *corroborated* (title similarity, then guid-anchored feed
lookup) and scored; a low score never silently proceeds — the resolve layer
raises ``NeedsConfirmation`` so the user vets the candidate first.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Optional

from ..crosswalk import score_match, title_similarity
from ..errors import UnresolvedLinkError
from ..models import EpisodeIdentity, SourceRef, Transcript
from .rss import Feed, FeedItem, load_feed

_UA = {"User-Agent": "OpenPod/0.1 (+https://github.com/openpod/openpod)"}


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8", "replace"))


def oembed_title(url: str) -> Optional[str]:
    """Episode title via Spotify's oEmbed endpoint (keyless, stable)."""
    try:
        data = _get_json("https://open.spotify.com/oembed?url="
                         + urllib.parse.quote(url, safe=""))
    except Exception:
        return None
    return data.get("title") or None


def search_itunes_episode(title: str, *, limit: int = 20) -> list[dict]:
    """Keyless episode search over the open catalog; returns raw results."""
    params = urllib.parse.urlencode({
        "term": title, "entity": "podcastEpisode", "limit": limit,
    })
    try:
        data = _get_json(f"https://itunes.apple.com/search?{params}")
    except Exception:
        return []
    return [r for r in (data.get("results") or [])
            if r.get("wrapperType") == "podcastEpisode"]


def identify_spotify(url: str) -> tuple[EpisodeIdentity, float, str]:
    """Resolve a Spotify episode URL to canonical RSS identity + confidence."""
    from .resolve import spotify_episode_id

    episode_id = spotify_episode_id(url)
    if not episode_id:
        raise UnresolvedLinkError(url, kind="spotify",
                                  tried=["spotify_episode_id"],
                                  hint="not a Spotify /episode/ URL")

    ident = EpisodeIdentity(spotify_episode_id=episode_id)
    ident.note("spotify_episode_id", "url")

    title = oembed_title(url)
    if not title:
        raise UnresolvedLinkError(
            url, kind="spotify", tried=["oembed"],
            hint="Spotify oEmbed returned no title; the episode may be "
                 "region-locked or removed. Supply the show's RSS feed or "
                 "a YouTube link for the same episode.")
    ident.title = title
    ident.note("title", "spotify-oembed")

    results = search_itunes_episode(title)
    best, best_score = None, 0.0
    for r in results:
        s = title_similarity(title, r.get("trackName") or "")
        if s > best_score:
            best, best_score = r, s
    if best is None or not best.get("feedUrl"):
        raise UnresolvedLinkError(
            url, kind="spotify", tried=["oembed", "itunes-episode-search"],
            hint=f"no open-catalog match for {title!r}; the show may be "
                 "Spotify-exclusive (no RSS feed exists). Ask the user for "
                 "an alternative source or a transcript file.")

    ms = best.get("trackTimeMillis")
    ident.feed_url = best.get("feedUrl")
    ident.rss_guid = best.get("episodeGuid")
    ident.enclosure_url = best.get("episodeUrl")
    ident.show = best.get("collectionName")
    ident.published = best.get("releaseDate")
    ident.duration = (ms / 1000.0) if ms else None
    ident.apple_episode_id = str(best.get("trackId") or "") or None
    ident.apple_show_id = str(best.get("collectionId") or "") or None

    confidence = round(best_score, 3)
    ident.note("feed_url", "itunes-episode-search", confidence)
    return ident, confidence, "itunes-episode-search"


def prepare_spotify(url: str) -> tuple[EpisodeIdentity, Feed, FeedItem, float, str]:
    """Resolve a Spotify URL down to its feed item, with match provenance."""
    ident, confidence, method = identify_spotify(url)
    feed = load_feed(ident.feed_url)

    item = None
    if ident.rss_guid:
        item = next((i for i in feed.items
                     if i.guid and i.guid.strip() == ident.rss_guid.strip()),
                    None)
    if item is None:
        best, best_score = None, 0.0
        for i in feed.items:
            s = score_match(title_a=ident.title, title_b=i.title,
                            duration_a=ident.duration,
                            published_a=ident.published,
                            published_b=i.published)
            if s > best_score:
                best, best_score = i, s
        item, confidence = best, min(confidence, best_score)
        method = "title-match"
    if item is None:
        raise UnresolvedLinkError(
            url, kind="spotify", tried=["oembed", "itunes-episode-search",
                                        "feed-match"],
            hint=f"episode not found in feed {ident.feed_url}")

    ident.rss_guid = ident.rss_guid or item.guid
    ident.enclosure_url = ident.enclosure_url or item.enclosure_url
    ident.published = ident.published or item.published
    ident.note("feed_item", method, confidence)
    return ident, feed, item, confidence, method


def ingest_spotify(url: str) -> tuple[SourceRef, Transcript]:
    """Full Spotify path: identify -> feed -> matched item -> transcript."""
    from .rss import ingest_podcast

    ident, feed, item, _confidence, _method = prepare_spotify(url)
    source, transcript = ingest_podcast(ident.feed_url, item=item,
                                        feed_title=feed.title or ident.show)
    return source, transcript
