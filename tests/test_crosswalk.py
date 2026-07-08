from openpod.crosswalk import (CONFIRM_THRESHOLD, Crosswalk, merge,
                               score_match, title_similarity)
from openpod.models import EpisodeIdentity


def _ident(**kw):
    base = dict(episode_key="k" * 8, key_confidence="guid",
                feed_url="https://example.com/feed.xml",
                rss_guid="ep-001", title="Episode One")
    base.update(kw)
    return EpisodeIdentity(**base)


def test_put_get_roundtrip(workspace):
    cw = Crosswalk(workspace)
    ident = _ident(spotify_episode_id="sp123")
    ident.note("spotify_episode_id", "url")
    cw.put(ident)
    got = cw.get(ident.episode_key)
    assert got is not None
    assert got.spotify_episode_id == "sp123"
    assert got.methods["spotify_episode_id"] == "url"


def test_find_by_platform_id(workspace):
    cw = Crosswalk(workspace)
    cw.put(_ident(apple_episode_id="1000123"))
    hit = cw.find_by("apple_episode_id", "1000123")
    assert hit is not None and hit.rss_guid == "ep-001"
    assert cw.find_by("apple_episode_id", "nope") is None


def test_put_merges_new_facts(workspace):
    cw = Crosswalk(workspace)
    cw.put(_ident(spotify_episode_id="sp123"))
    cw.put(_ident(youtube_video_id="ytABC"))
    got = cw.get("k" * 8)
    assert got.spotify_episode_id == "sp123"      # old fact survived
    assert got.youtube_video_id == "ytABC"        # new fact merged


def test_merge_keeps_worst_confidence():
    a = _ident()
    a.match_confidence = 0.9
    b = _ident()
    b.match_confidence = 0.6
    assert merge(a, b).match_confidence == 0.6


def test_show_table(workspace):
    cw = Crosswalk(workspace)
    cw.put_show("https://example.com/feed.xml", apple_show_id="99",
                show="Test Pod")
    rec = cw.get_show(apple_show_id="99")
    assert rec and rec["feed_url"] == "https://example.com/feed.xml"
    # merge on upsert
    cw.put_show("https://example.com/feed.xml", spotify_show_id="sp-show")
    rec = cw.get_show(feed_url="https://example.com/feed.xml")
    assert rec["apple_show_id"] == "99" and rec["spotify_show_id"] == "sp-show"


def test_title_similarity_variants():
    assert title_similarity("Ep. 42: The Future of AI",
                            "The Future of AI") == 1.0
    assert title_similarity("#42 - The Future of AI",
                            "the future of ai") == 1.0
    assert title_similarity("totally different", "another thing") < 0.6


def test_score_match_corroboration():
    # Same title, agreeing duration and date -> passes the gate.
    s = score_match(title_a="Episode One", title_b="Episode One",
                    duration_a=3600, duration_b=3650,
                    published_a="2026-07-01", published_b="Tue, 01 Jul 2026 10:00:00 GMT")
    assert s >= CONFIRM_THRESHOLD
    # Same title but contradicting duration -> penalized below the gate.
    s2 = score_match(title_a="Episode One", title_b="Episode One",
                     duration_a=3600, duration_b=1200)
    assert s2 < CONFIRM_THRESHOLD
    # Date far apart also penalizes.
    s3 = score_match(title_a="Episode One", title_b="Episode One",
                     published_a="2026-07-01", published_b="2026-01-01")
    assert s3 < 1.0
