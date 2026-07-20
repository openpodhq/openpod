"""Build "jump to the moment" deep-links — capability-aware, never silent.

Apps differ in what a link can actually do, so link-building consults a
capability table and returns a structured :class:`LinkResult`: the URL, plus
whether the timestamp survived and an honest note when it didn't ("Apple
opens the episode, not the moment"). Degradation is data the interface
surfaces, never a silent downgrade.

Target selection: explicit ``app`` arg → the workspace's
``preferred_playback_app`` setting → the platform the user's link came from
(origin) → the transcript source. The user's platform wins by default —
what we used to *get* the transcript is an implementation detail.

We *navigate*, we never re-host. That is the legal thesis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .identity import episode_key as _episode_key
from .models import EpisodeIdentity, SourceRef

# The hosted player that claims OpenPod's own moment links — mirrors the
# share.ts momentUrl()/`/e/<episode_key>` path-form contract. An HTTPS origin
# we own, so the link resolves inside OpenPod instead of the OS podcast app.
PLAYER_ORIGIN = "https://player.openpod.dev"

# Per-app capabilities: what identifier the link needs, whether a mid-episode
# timestamp is honored, and how the URL is built.
APP_CAPABILITIES = {
    "youtube": {"needs": "youtube_video_id", "timestamp": True},
    "spotify": {"needs": "spotify_episode_id", "timestamp": True},
    "apple": {"needs": "apple_episode_id", "timestamp": False},
    "overcast": {"needs": "enclosure_url", "timestamp": True},
    # OpenPod's own hosted player: an HTTPS link we own that resolves the
    # episode by feed + guid/episode_key and seeks to t. This is the safe
    # default — it keeps the listener inside OpenPod.
    "openpod": {"needs": "feed_url", "timestamp": True},
    # The open enclosure with a #t= media fragment. A bare enclosure resolves
    # to audio/mpeg, so the OS hands it to the *default podcast app*, not our
    # player — it leaks the listener out of OpenPod. Explicit opt-in only
    # (preferred_app="podcast"), never the automatic fallback.
    "podcast": {"needs": "enclosure_url", "timestamp": True},
}


@dataclass
class LinkResult:
    """A deep-link plus the honest truth about what it can do."""

    url: Optional[str]
    app: str
    timestamp_supported: bool = True
    precision: str = "exact"          # "exact" | "approximate" | "none"
    note: Optional[str] = None        # degrade note for the interface to show

    def to_dict(self) -> dict:
        return {k: v for k, v in vars(self).items() if v is not None}


def _identity_from_source(source: SourceRef) -> EpisodeIdentity:
    ident = EpisodeIdentity()
    if source.kind == "youtube":
        ident.youtube_video_id = source.video_id
    elif source.kind == "spotify":
        ident.spotify_episode_id = source.episode_id
    ident.enclosure_url = source.audio_url
    # Feed identity for the OpenPod player link. A podcast SourceRef carries
    # the feed (or episode page) URL and the RSS <guid>; the player resolves
    # the episode from these, so the link never needs the raw enclosure.
    ident.rss_guid = source.guid
    ident.title = source.title
    ident.published = source.published
    if source.kind == "podcast":
        ident.feed_url = source.url
    return ident


def _pick_app(identity: EpisodeIdentity, *, app: Optional[str],
              preferred: Optional[str], origin_kind: Optional[str],
              source_kind: Optional[str]) -> str:
    # An explicit choice (arg or workspace setting) may target any app,
    # including the raw-enclosure "podcast" opt-in.
    for candidate in (app, preferred):
        if candidate in APP_CAPABILITIES:
            return candidate
    # Inferred targets (where the link came from / the transcript source) never
    # select the raw enclosure — a podcast origin/source maps to our own
    # player, which keeps the listener inside OpenPod.
    for candidate in (origin_kind, source_kind):
        if candidate in APP_CAPABILITIES and candidate != "podcast":
            return candidate
    # Default to our own player — never the raw enclosure, which leaks the
    # listener to the OS default podcast app.
    return "openpod"


def build_link(source: Optional[SourceRef], seconds: float, *,
               identity: Optional[EpisodeIdentity] = None,
               app: Optional[str] = None,
               origin: Optional[SourceRef] = None,
               preferred_app: Optional[str] = None) -> LinkResult:
    """Build the best link to ``seconds`` for the user's app.

    Falls back down the app's capability ladder explicitly: a target app
    that can't take a timestamp still gets an episode-level link, with the
    degradation stated in ``note``; a target we hold no identifier for
    falls back to another platform we *can* link, also stated.
    """
    t = max(0, int(round(seconds)))
    ident = identity or (_identity_from_source(source) if source else EpisodeIdentity())
    if source is not None and ident.enclosure_url is None:
        ident.enclosure_url = source.audio_url

    target = _pick_app(ident, app=app, preferred=preferred_app,
                       origin_kind=origin.kind if origin else None,
                       source_kind=source.kind if source else None)

    result = _render(target, ident, t, origin=origin)
    if result.url is not None:
        return result

    # No identifier for the target app — fall back, and say so. "podcast" (the
    # raw enclosure) is deliberately absent: a missing platform id degrades to
    # our own player link, never to a link that opens outside OpenPod.
    for fallback in ("youtube", "spotify", "openpod", "apple"):
        if fallback == target:
            continue
        r = _render(fallback, ident, t, origin=origin)
        if r.url is not None:
            r.note = (f"no {target} link available for this episode — "
                      f"falling back to {fallback}"
                      + (f"; {r.note}" if r.note else ""))
            return r

    return LinkResult(url=None, app=target, timestamp_supported=False,
                      precision="none",
                      note="no navigable public link for this source")


def _render(app: str, ident: EpisodeIdentity, t: int,
            origin: Optional[SourceRef] = None) -> LinkResult:
    if app == "youtube" and ident.youtube_video_id:
        return LinkResult(
            url=f"https://www.youtube.com/watch?v={ident.youtube_video_id}&t={t}s",
            app=app)

    if app == "spotify" and ident.spotify_episode_id:
        return LinkResult(
            url=f"https://open.spotify.com/episode/{ident.spotify_episode_id}?t={t}",
            app=app)

    if app == "apple":
        url = None
        if ident.apple_show_id and ident.apple_episode_id:
            country = ident.apple_country or "us"
            url = (f"https://podcasts.apple.com/{country}/podcast/"
                   f"id{ident.apple_show_id}?i={ident.apple_episode_id}")
        elif origin is not None and origin.kind == "apple" and origin.url:
            url = origin.url
        if url:
            return LinkResult(
                url=url, app=app, timestamp_supported=False, precision="none",
                note="Apple Podcasts links open the episode, not the moment — "
                     "no public timestamp parameter exists")

    if app == "overcast" and ident.enclosure_url:
        from urllib.parse import quote
        return LinkResult(
            url=("overcast://x-callback-url/add?url="
                 + quote(ident.enclosure_url, safe="")),
            app=app, timestamp_supported=True,
            note="opens in Overcast via x-callback-url")

    if app == "openpod" and ident.feed_url:
        from urllib.parse import quote, urlencode
        # Prefer the crosswalked key; else derive it exactly as the player does
        # (openpod.identity.episode_key), so the ek fallback matches byte-for-byte.
        key = ident.episode_key or _episode_key(
            ident.feed_url, guid=ident.rss_guid, audio_url=ident.enclosure_url,
            title=ident.title, published=ident.published)[0]
        params = [("feed", ident.feed_url)]
        if ident.rss_guid:
            params.append(("guid", ident.rss_guid))
        params.append(("t", str(t)))
        return LinkResult(
            url=f"{PLAYER_ORIGIN}/e/{key}?{urlencode(params, quote_via=quote)}",
            app=app, timestamp_supported=True,
            note="opens in the OpenPod player at the moment")

    if app == "podcast" and ident.enclosure_url:
        sep = "&" if "#" in ident.enclosure_url else "#"
        return LinkResult(
            url=f"{ident.enclosure_url}{sep}t={t}", app=app,
            note="the #t= fragment seeks inside OpenPod's player and most "
                 "browsers; a bare media player may ignore it")

    return LinkResult(url=None, app=app, timestamp_supported=False,
                      precision="none")


def build_deeplink(source: SourceRef, seconds: float) -> Optional[str]:
    """Back-compat wrapper: URL-or-None, transcript-source platform.

    Prefer :func:`build_link` — it honors origin/preference and reports
    capability honestly instead of returning a bare string.

    A podcast source now defaults to the OpenPod *player* link (feed + guid),
    not the raw enclosure: the enclosure resolved to audio/mpeg and leaked the
    listener to the OS default podcast app. Pass ``preferred_app="podcast"`` to
    :func:`build_link` to opt back into the raw enclosure explicitly.
    """
    if source is None:
        return None
    # The old contract returned None for apple/file/unknown sources.
    if source.kind not in ("youtube", "spotify", "podcast"):
        return None
    # youtube/spotify keep their native platform link; a podcast takes the
    # default target (now "openpod"), never the raw enclosure.
    app = source.kind if source.kind in ("youtube", "spotify") else None
    return build_link(source, seconds, app=app).url
