import pytest

from openpod.deeplink import build_deeplink, build_link
from openpod.errors import UnsupportedFormatError
from openpod.ingest import validate
from openpod.ingest.validate import MediaInfo, classify_bytes, classify_mime, ensure_media
from openpod.models import EpisodeIdentity, SourceRef


# -- content-type validation --------------------------------------------------- #

def test_classify_mime_matrix():
    assert classify_mime("audio/mpeg") == "audio"
    assert classify_mime("audio/mpeg; charset=binary") == "audio"
    assert classify_mime("video/mp4") == "video"
    assert classify_mime("application/rss+xml") == "feed"
    assert classify_mime("text/html; charset=utf-8") == "html"
    assert classify_mime(None) == "unknown"


def test_classify_bytes_magic():
    assert classify_bytes(b"ID3\x04...") == "audio/mpeg"
    assert classify_bytes(b"OggS....") == "audio/ogg"
    assert classify_bytes(b"<!DOCTYPE html><html>") == "text/html"
    assert classify_bytes(b"\x00\x00\x00\x18ftypM4A ") == "audio/mp4"
    assert classify_bytes(b"\x00\x00\x00\x18ftypisom") == "video/mp4"


def test_ensure_media_rejects_html(monkeypatch):
    monkeypatch.setattr(validate, "sniff", lambda url, **kw: MediaInfo(
        url=url, content_type="text/html", kind="html"))
    with pytest.raises(UnsupportedFormatError) as ei:
        ensure_media("https://example.com/page")
    d = ei.value.to_dict()
    assert d["error"] == "unsupported_format"
    assert d["content_type"] == "text/html"


def test_ensure_media_resolves_redirector(monkeypatch):
    # chrt.fm-style: the terminal URL is what gets cached and fetched.
    monkeypatch.setattr(validate, "sniff", lambda url, **kw: MediaInfo(
        url="https://real.cdn/ep.mp3", content_type="audio/mpeg", kind="audio"))
    info = ensure_media("https://chrt.fm/track/XYZ/real.cdn/ep.mp3")
    assert info.url == "https://real.cdn/ep.mp3"


# -- capability-aware links ----------------------------------------------------- #

def _identity():
    return EpisodeIdentity(
        youtube_video_id="ytABC", spotify_episode_id="sp123",
        apple_show_id="99", apple_episode_id="1000777", apple_country="il",
        enclosure_url="https://cdn/ep.mp3")


def test_origin_platform_wins_over_transcript_source():
    # Transcript came from YouTube, user pasted Spotify -> Spotify link back.
    source = SourceRef(kind="youtube", video_id="ytABC")
    origin = SourceRef(kind="spotify", episode_id="sp123")
    r = build_link(source, 754, identity=_identity(), origin=origin)
    assert r.app == "spotify"
    assert r.url == "https://open.spotify.com/episode/sp123?t=754"
    assert r.timestamp_supported


def test_preferred_app_wins_over_origin():
    source = SourceRef(kind="youtube", video_id="ytABC")
    origin = SourceRef(kind="spotify", episode_id="sp123")
    r = build_link(source, 60, identity=_identity(), origin=origin,
                   preferred_app="youtube")
    assert r.app == "youtube" and "watch?v=ytABC&t=60s" in r.url


def test_apple_degrades_honestly():
    r = build_link(None, 3335, identity=_identity(), app="apple")
    assert r.url == "https://podcasts.apple.com/il/podcast/id99?i=1000777"
    assert r.timestamp_supported is False
    assert r.precision == "none"
    assert "not the moment" in r.note


def test_fallback_when_target_id_missing_states_it():
    ident = EpisodeIdentity(youtube_video_id="ytABC")   # no spotify id
    r = build_link(None, 30, identity=ident, app="spotify")
    assert r.url is not None
    assert r.app == "youtube"
    assert "falling back" in r.note


def test_enclosure_fragment_notes_player_caveat():
    ident = EpisodeIdentity(enclosure_url="https://cdn/ep.mp3")
    r = build_link(None, 30, identity=ident, app="podcast")
    assert r.url == "https://cdn/ep.mp3#t=30"
    assert "player" in r.note


def test_no_link_at_all_is_explicit():
    r = build_link(SourceRef(kind="file"), 10)
    assert r.url is None and r.precision == "none"


def test_backcompat_build_deeplink_unchanged():
    src = SourceRef(kind="youtube", video_id="abc123")
    assert build_deeplink(src, 754) == "https://www.youtube.com/watch?v=abc123&t=754s"
    assert build_deeplink(SourceRef(kind="file"), 10) is None


# -- catch persists origin + crosswalk ------------------------------------------ #

def test_catch_meta_carries_origin_and_key(workspace, vtt_file):
    from openpod.catch import catch

    result = catch("https://open.spotify.com/episode/abc123",
                   workspace=workspace, kind="spotify",
                   transcript_path=str(vtt_file))
    meta = result.entry.read_meta()
    assert meta["origin"]["kind"] == "spotify"
    assert meta["origin"]["episode_id"] == "abc123"
    # identity was recorded even though the transcript came from a local file
    assert result.origin.kind == "spotify"
