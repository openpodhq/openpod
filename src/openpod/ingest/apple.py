"""Apple Podcasts ingestion — iTunes Lookup, not page scraping.

An Apple episode URL carries everything needed for deterministic resolution:

    https://podcasts.apple.com/il/podcast/<slug>/id{showId}?i={episodeId}
                              ^^ country              ^^^^^^     ^^^^^^^^

The iTunes Lookup API (keyless) turns those ids into the show's ``feedUrl``
and — looked up by the episode id directly — the episode's ``episodeGuid``,
so the specific episode is matched in the feed by guid, not guessed by
recency. ``country`` matters: Apple's catalog is regional and a lookup
without it can miss shows that exist only in one storefront.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from ..crosswalk import score_match
from ..errors import UnresolvedLinkError
from ..models import EpisodeIdentity, SourceRef, Transcript
from .rss import Feed, FeedItem, load_feed

_UA = {"User-Agent": "OpenPod/0.1 (+https://github.com/openpod/openpod)"}

APPLE_HOSTS = {"podcasts.apple.com", "itunes.apple.com", "music.apple.com"}

_url_re = re.compile(
    r"podcasts\.apple\.com/(?:(?P<country>[a-z]{2})/)?podcast/(?:[^/]+/)?id(?P<show_id>\d+)",
    re.I,
)


@dataclass
class AppleRef:
    show_id: str
    episode_id: Optional[str] = None   # the `i=` query param
    country: Optional[str] = None


def parse_apple_url(url: str) -> Optional[AppleRef]:
    m = _url_re.search(url)
    if not m:
        # itunes.apple.com legacy form: /podcast/id123 with optional /{cc}/
        m = re.search(
            r"itunes\.apple\.com/(?:(?P<country>[a-z]{2})/)?podcast/(?:[^/]+/)?id(?P<show_id>\d+)",
            url, re.I)
        if not m:
            return None
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    episode_id = (q.get("i") or [None])[0]
    return AppleRef(show_id=m.group("show_id"), episode_id=episode_id,
                    country=(m.group("country") or "").lower() or None)


def _lookup(params: dict) -> list[dict]:
    """Call the iTunes Lookup API; returns the results list (may be empty)."""
    url = "https://itunes.apple.com/lookup?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8", "replace"))
    return data.get("results") or []


def identify_apple(url: str) -> EpisodeIdentity:
    """Resolve an Apple URL to an :class:`EpisodeIdentity` — no scraping,
    no search, no guessing by recency."""
    ref = parse_apple_url(url)
    if ref is None:
        raise UnresolvedLinkError(url, kind="apple",
                                  tried=["parse_apple_url"],
                                  hint="not a recognizable Apple Podcasts URL")

    ident = EpisodeIdentity(apple_show_id=ref.show_id,
                            apple_episode_id=ref.episode_id,
                            apple_country=ref.country)
    ident.note("apple_show_id", "url")

    country = ref.country or "us"

    # Episode id: the Lookup API takes *show* id + entity=podcastEpisode and
    # returns recent episodes with trackId + episodeGuid — match the `i=` id
    # there. (Episode trackIds are not directly lookup-able.)
    if ref.episode_id:
        ident.note("apple_episode_id", "url")
        results = _lookup({"id": ref.show_id, "entity": "podcastEpisode",
                           "limit": 200, "country": country})
        ep = next((r for r in results
                   if r.get("wrapperType") == "podcastEpisode"
                   and str(r.get("trackId")) == str(ref.episode_id)), None)
        if ep:
            ident.feed_url = ep.get("feedUrl") or ident.feed_url
            ident.rss_guid = ep.get("episodeGuid") or ident.rss_guid
            ident.enclosure_url = ep.get("episodeUrl") or ident.enclosure_url
            ident.title = ep.get("trackName") or ident.title
            ident.show = ep.get("collectionName") or ident.show
            ident.published = ep.get("releaseDate") or ident.published
            ms = ep.get("trackTimeMillis")
            ident.duration = (ms / 1000.0) if ms else ident.duration
            if ident.rss_guid:
                ident.note("rss_guid", "apple-lookup")
            if ident.feed_url:
                ident.note("feed_url", "apple-lookup")

    # Show lookup fills the feed URL when the episode lookup didn't.
    if not ident.feed_url:
        results = _lookup({"id": ref.show_id, "country": country})
        show = next((r for r in results
                     if r.get("kind") == "podcast"
                     or r.get("wrapperType") == "track"), None)
        if show:
            ident.feed_url = show.get("feedUrl")
            ident.show = ident.show or show.get("collectionName")
            if ident.feed_url:
                ident.note("feed_url", "apple-lookup")

    if not ident.feed_url:
        raise UnresolvedLinkError(
            url, kind="apple",
            tried=[f"itunes-lookup:id={ref.episode_id or ref.show_id},country={country}"],
            hint="Apple returned no feedUrl for this show; the show may be "
                 "region-locked — try the country code from the original URL, "
                 "or supply the RSS feed directly.")
    return ident


def find_feed_item(feed: Feed, ident: EpisodeIdentity) -> tuple[Optional[FeedItem], float, str]:
    """Locate ``ident``'s episode in its feed.

    Returns ``(item, confidence, method)``. Guid match is deterministic
    (1.0); otherwise fall back to corroborated title/date scoring — never
    "newest entry".
    """
    if ident.rss_guid:
        for item in feed.items:
            if item.guid and item.guid.strip() == ident.rss_guid.strip():
                return item, 1.0, "feed-guid"

    best, best_score = None, 0.0
    for item in feed.items:
        s = score_match(title_a=ident.title, title_b=item.title,
                        published_a=ident.published, published_b=item.published)
        if s > best_score:
            best, best_score = item, s
    return best, best_score, "title-match"


def prepare_apple(url: str) -> tuple[EpisodeIdentity, Feed, FeedItem, float, str]:
    """Resolve an Apple URL down to its feed item, with match provenance.

    Returns ``(identity, feed, item, confidence, method)`` so the caller
    (:func:`openpod.ingest.resolve.resolve`) can enforce the confirmation
    gate before any ingestion happens.
    """
    ident = identify_apple(url)
    feed = load_feed(ident.feed_url)
    item, confidence, method = find_feed_item(feed, ident)
    if item is None:
        raise UnresolvedLinkError(
            url, kind="apple", tried=["itunes-lookup", "feed-match"],
            hint=f"episode not found in feed {ident.feed_url}")
    ident.rss_guid = ident.rss_guid or item.guid
    ident.enclosure_url = ident.enclosure_url or item.enclosure_url
    ident.title = ident.title or item.title
    ident.published = ident.published or item.published
    ident.show = ident.show or feed.title
    ident.note("feed_item", method, confidence)
    return ident, feed, item, confidence, method


def ingest_apple(url: str) -> tuple[SourceRef, Transcript]:
    """Full Apple path: identify -> feed -> matched item -> transcript."""
    from .rss import ingest_podcast

    ident, feed, item, _confidence, _method = prepare_apple(url)
    source, transcript = ingest_podcast(ident.feed_url, item=item,
                                        feed_title=feed.title or ident.show)
    return source, transcript
