import pytest

from openpod.errors import NeedsConfirmation, UnresolvedLinkError
from openpod.ingest import apple as apple_mod
from openpod.ingest import spotify as spotify_mod
from openpod.ingest.apple import find_feed_item, parse_apple_url
from openpod.ingest.resolve import detect_kind, origin_ref, resolve, resolve_full
from openpod.ingest.rss import Feed, FeedItem
from openpod.models import EpisodeIdentity


# -- detect_kind ------------------------------------------------------------ #

def test_detect_kind_apple():
    assert detect_kind(
        "https://podcasts.apple.com/il/podcast/invested/id1234567?i=1000999") == "apple"
    assert detect_kind("https://itunes.apple.com/us/podcast/id555") == "apple"


def test_detect_kind_no_silent_default():
    # An arbitrary web page is NOT quietly treated as a podcast feed.
    assert detect_kind("https://example.com/some/article") == "unknown"
    # But explicit feed/audio shapes still classify.
    assert detect_kind("https://example.com/feed.xml") == "podcast"
    assert detect_kind("https://cdn.example.com/ep.mp3") == "podcast"


def test_unknown_kind_raises_structured():
    with pytest.raises(UnresolvedLinkError) as ei:
        resolve("https://example.com/some/article")
    d = ei.value.to_dict()
    assert d["error"] == "unresolved_link"
    assert d["kind"] == "unknown"
    assert "hint" in d


# -- apple URL parsing -------------------------------------------------------- #

def test_parse_apple_url_full():
    ref = parse_apple_url(
        "https://podcasts.apple.com/il/podcast/invested/id1234567?i=1000888777")
    assert ref.show_id == "1234567"
    assert ref.episode_id == "1000888777"
    assert ref.country == "il"


def test_parse_apple_url_show_only_no_country():
    ref = parse_apple_url("https://podcasts.apple.com/podcast/id42")
    assert ref.show_id == "42" and ref.episode_id is None and ref.country is None
    assert parse_apple_url("https://example.com/nope") is None


def _feed():
    return Feed(title="Test Pod", items=[
        FeedItem(title="Episode Two: Vector DBs", guid="ep-002",
                 published="Tue, 08 Jul 2026 10:00:00 GMT",
                 enclosure_url="https://cdn/ep2.mp3",
                 transcript_url=None, transcript_type=None),
        FeedItem(title="Episode One: Consensus", guid="ep-001",
                 published="Tue, 01 Jul 2026 10:00:00 GMT",
                 enclosure_url="https://cdn/ep1.mp3",
                 transcript_url=None, transcript_type=None),
    ])


def test_find_feed_item_by_guid_never_newest():
    ident = EpisodeIdentity(rss_guid="ep-001", title="wrong title on purpose")
    item, conf, method = find_feed_item(_feed(), ident)
    assert item.guid == "ep-001"          # NOT the newest entry
    assert conf == 1.0 and method == "feed-guid"


def test_find_feed_item_title_fallback_scored():
    ident = EpisodeIdentity(title="Episode One: Consensus",
                            published="2026-07-01")
    item, conf, method = find_feed_item(_feed(), ident)
    assert item.guid == "ep-001"
    assert method == "title-match" and conf >= 0.8


# -- spotify chain (network monkeypatched) ------------------------------------ #

def test_identify_spotify(monkeypatch):
    monkeypatch.setattr(spotify_mod, "oembed_title",
                        lambda url: "Episode One: Consensus")
    monkeypatch.setattr(spotify_mod, "search_itunes_episode", lambda title, **kw: [{
        "wrapperType": "podcastEpisode",
        "trackName": "Episode One: Consensus",
        "collectionName": "Test Pod",
        "feedUrl": "https://example.com/feed.xml",
        "episodeGuid": "ep-001",
        "episodeUrl": "https://cdn/ep1.mp3",
        "releaseDate": "2026-07-01T10:00:00Z",
        "trackTimeMillis": 3600000,
        "trackId": 77, "collectionId": 88,
    }])
    ident, conf, method = spotify_mod.identify_spotify(
        "https://open.spotify.com/episode/abc123")
    assert ident.spotify_episode_id == "abc123"
    assert ident.feed_url == "https://example.com/feed.xml"
    assert ident.rss_guid == "ep-001"
    assert ident.apple_show_id == "88"
    assert conf == 1.0 and method == "itunes-episode-search"


def test_identify_spotify_exclusive_raises_structured(monkeypatch):
    monkeypatch.setattr(spotify_mod, "oembed_title", lambda url: "Some Exclusive")
    monkeypatch.setattr(spotify_mod, "search_itunes_episode",
                        lambda title, **kw: [])
    with pytest.raises(UnresolvedLinkError) as ei:
        spotify_mod.identify_spotify("https://open.spotify.com/episode/xyz")
    assert "itunes-episode-search" in ei.value.tried


# -- confirmation gate --------------------------------------------------------- #

def _prep_low_confidence(monkeypatch, confidence):
    ident = EpisodeIdentity(feed_url="https://example.com/feed.xml",
                            title="Episode One: Consensus", show="Test Pod")
    feed = _feed()
    item = feed.items[1]

    def fake_prepare(url):
        return ident, feed, item, confidence, "title-match"

    monkeypatch.setattr("openpod.ingest.spotify.prepare_spotify", fake_prepare)
    monkeypatch.setattr(
        "openpod.ingest.rss.ingest_podcast",
        lambda feed_url, item=None, feed_title=None: (
            origin_ref("https://example.com/feed.xml", "podcast"),
            _tiny_transcript()))


def _tiny_transcript():
    from openpod.models import Cue, Transcript
    return Transcript(cues=[Cue(start=0, end=5, text="hello world")])


def test_gate_blocks_fuzzy_match(monkeypatch):
    _prep_low_confidence(monkeypatch, confidence=0.55)
    with pytest.raises(NeedsConfirmation) as ei:
        resolve_full("https://open.spotify.com/episode/abc")
    d = ei.value.to_dict()
    assert d["error"] == "needs_confirmation"
    assert d["candidate"]["title"] == "Episode One: Consensus"
    assert d["confidence"] == 0.55
    assert "confirm" in d["next_step"].lower()


def test_gate_passes_when_confirmed(monkeypatch):
    _prep_low_confidence(monkeypatch, confidence=0.55)
    r = resolve_full("https://open.spotify.com/episode/abc", confirmed=True)
    assert r.identity.match_confidence == 1.0
    assert r.identity.methods["feed_item"] == "user-confirmed"


def test_gate_skipped_for_deterministic_match(monkeypatch):
    _prep_low_confidence(monkeypatch, confidence=1.0)
    r = resolve_full("https://open.spotify.com/episode/abc")
    assert r.confidence == 1.0


# -- origin decoupling --------------------------------------------------------- #

def test_origin_ref_preserves_pasted_platform():
    o = origin_ref("https://open.spotify.com/episode/abc123")
    assert o.kind == "spotify" and o.episode_id == "abc123"
    o2 = origin_ref("https://youtu.be/xyz")
    assert o2.kind == "youtube" and o2.video_id == "xyz"


def test_resolution_carries_origin(monkeypatch):
    _prep_low_confidence(monkeypatch, confidence=1.0)
    r = resolve_full("https://open.spotify.com/episode/abc")
    assert r.origin.kind == "spotify"
    assert r.origin.episode_id == "abc"
    # transcript source may be a different platform — that's the point
    assert r.source.kind == "podcast"


# -- crosswalk cache shortcut ---------------------------------------------------- #

def test_cached_identity_skips_reidentification(monkeypatch, workspace):
    from openpod.crosswalk import Crosswalk

    cw = Crosswalk(workspace)
    ident = EpisodeIdentity(episode_key="e" * 8, feed_url="https://example.com/feed.xml",
                            rss_guid="ep-001", spotify_episode_id="abc",
                            title="Episode One: Consensus")
    cw.put(ident)

    def boom(url):
        raise AssertionError("re-identified a cached episode over the network")

    monkeypatch.setattr("openpod.ingest.spotify.prepare_spotify", boom)
    monkeypatch.setattr("openpod.ingest.rss.load_feed", lambda url: _feed())
    monkeypatch.setattr(
        "openpod.ingest.rss.ingest_podcast",
        lambda feed_url, item=None, feed_title=None: (
            origin_ref("https://example.com/feed.xml", "podcast"),
            _tiny_transcript()))

    r = resolve_full("https://open.spotify.com/episode/abc", crosswalk=cw)
    assert r.method == "crosswalk-cache"
    assert r.identity.episode_key == "e" * 8
