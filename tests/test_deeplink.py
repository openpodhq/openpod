from openpod.deeplink import build_deeplink, build_link
from openpod.identity import episode_key
from openpod.models import SourceRef


def test_youtube_deeplink():
    src = SourceRef(kind="youtube", video_id="abc123")
    assert build_deeplink(src, 754) == "https://www.youtube.com/watch?v=abc123&t=754s"


def test_spotify_deeplink():
    src = SourceRef(kind="spotify", episode_id="xyz")
    assert build_deeplink(src, 1450.7) == "https://open.spotify.com/episode/xyz?t=1451"


def test_podcast_defaults_to_openpod_player_link():
    # A podcast source now resolves to the OpenPod player (feed + guid), not the
    # raw enclosure — the enclosure opened in the OS default podcast app.
    src = SourceRef(kind="podcast", url="https://example.com/feed", guid="ep-001",
                    audio_url="https://cdn.example.com/ep1.mp3")
    key = episode_key("https://example.com/feed", guid="ep-001")[0]
    assert build_deeplink(src, 65) == (
        f"https://player.openpod.dev/e/{key}"
        "?feed=https%3A%2F%2Fexample.com%2Ffeed&guid=ep-001&t=65"
    )


def test_podcast_never_leaks_the_raw_enclosure_by_default():
    # No feed to resolve (a bare enclosure) → no link at all, rather than a raw
    # audio/mpeg URL the OS would hand to Apple Podcasts / Overcast / Spotify.
    src = SourceRef(kind="podcast", audio_url="https://cdn/ep.mp3")
    assert build_deeplink(src, 30) is None


def test_raw_enclosure_is_opt_in_only():
    # The raw #t= enclosure remains reachable, but only via an explicit choice.
    src = SourceRef(kind="podcast", url="https://f/rss", guid="g",
                    audio_url="https://cdn/ep.mp3")
    r = build_link(src, 30, preferred_app="podcast")
    assert r.app == "podcast"
    assert r.url == "https://cdn/ep.mp3#t=30"


def test_no_deeplink_when_unavailable():
    assert build_deeplink(SourceRef(kind="file"), 10) is None
    assert build_deeplink(SourceRef(kind="youtube"), 10) is None  # no id
