from openpod.deeplink import build_deeplink
from openpod.models import SourceRef


def test_youtube_deeplink():
    src = SourceRef(kind="youtube", video_id="abc123")
    assert build_deeplink(src, 754) == "https://www.youtube.com/watch?v=abc123&t=754s"


def test_spotify_deeplink():
    src = SourceRef(kind="spotify", episode_id="xyz")
    assert build_deeplink(src, 1450.7) == "https://open.spotify.com/episode/xyz?t=1451"


def test_podcast_media_fragment():
    src = SourceRef(kind="podcast", audio_url="https://cdn/ep.mp3")
    assert build_deeplink(src, 30) == "https://cdn/ep.mp3#t=30"


def test_no_deeplink_when_unavailable():
    assert build_deeplink(SourceRef(kind="file"), 10) is None
    assert build_deeplink(SourceRef(kind="youtube"), 10) is None  # no id
