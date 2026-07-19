"""RSS ingestion: metadata extraction (description / duration / chapters) and
the transient-failure retry on ``_fetch``."""

import json
import urllib.error

import pytest

from openpod.ingest import rss


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

_VTT = """WEBVTT

00:00:00.000 --> 00:00:04.000
Opening line.

00:00:04.000 --> 00:00:09.000
A second line about the topic.
"""

_FEED_RICH = """<?xml version="1.0"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Rich Pod</title>
    <item>
      <title>Ep 1</title>
      <guid>ep-1</guid>
      <pubDate>Tue, 01 Jul 2026 10:00:00 GMT</pubDate>
      <link>https://example.com/ep1</link>
      <description>Notes.
0:00 Intro
10:00 The middle
20:00 The end</description>
      <itunes:duration>1:02:03</itunes:duration>
      <enclosure url="https://example.com/ep1.mp3" type="audio/mpeg"/>
      <podcast:chapters url="https://example.com/ep1-chapters.json"
                        type="application/json+chapters"/>
    </item>
  </channel>
</rss>
"""

_CHAPTERS_JSON = json.dumps({
    "version": "1.2.0",
    "chapters": [
        {"startTime": 0, "title": "Cold open"},
        {"startTime": 90.5, "title": "Main topic", "endTime": 600},
        {"startTime": 600, "title": "Wrap"},
    ],
})


class _FakeResp:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --------------------------------------------------------------------------- #
# _parse_duration
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw,expected", [
    ("1:02:03", 3723.0),
    ("12:30", 750.0),
    ("3723", 3723.0),
    ("3723.5", 3723.5),
    (95, 95.0),
    (None, None),
    ("", None),
    ("   ", None),
    ("not-a-duration", None),
])
def test_parse_duration(raw, expected):
    assert rss._parse_duration(raw) == expected


# --------------------------------------------------------------------------- #
# Field extraction — both parse paths
# --------------------------------------------------------------------------- #

def test_parse_feed_extracts_rich_metadata():
    """feedparser path: description, itunes:duration, podcast:chapters url."""
    feed = rss.parse_feed(_FEED_RICH)
    assert feed.title == "Rich Pod"
    item = feed.items[0]
    assert item.description and "Intro" in item.description
    assert item.duration == 3723.0
    assert item.chapters_url == "https://example.com/ep1-chapters.json"


def test_parse_feed_stdlib_extracts_rich_metadata():
    """stdlib fallback path extracts the same new fields."""
    feed = rss._parse_feed_stdlib(_FEED_RICH)
    item = feed.items[0]
    assert item.description and "The middle" in item.description
    assert item.duration == 3723.0
    assert item.chapters_url == "https://example.com/ep1-chapters.json"
    # ...and still the fields it always carried.
    assert item.guid == "ep-1"
    assert item.enclosure_url == "https://example.com/ep1.mp3"


def test_stdlib_itunes_summary_fallback_for_description():
    feed = rss._parse_feed_stdlib(
        '<?xml version="1.0"?>'
        '<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<channel><title>T</title><item><title>E</title>"
        "<itunes:summary>Summary only</itunes:summary>"
        '<enclosure url="https://x/e.mp3"/></item></channel></rss>'
    )
    assert feed.items[0].description == "Summary only"


# --------------------------------------------------------------------------- #
# ingest_podcast wires duration + chapters onto the SourceRef
# --------------------------------------------------------------------------- #

def test_ingest_sets_duration_from_item(monkeypatch):
    monkeypatch.setattr(rss, "_fetch", lambda url, *a, **k: _VTT.encode())
    item = rss.FeedItem(
        title="Ep", guid="g", published=None,
        enclosure_url="https://x/e.mp3",
        transcript_url="https://x/e.vtt", transcript_type="text/vtt",
        duration=2400.0,
    )
    source, transcript = rss.ingest_podcast("https://x/feed.xml", item=item)
    assert source.duration == 2400.0
    assert len(transcript) == 2  # came back via the publisher-transcript path


def test_ingest_chapters_from_description(monkeypatch):
    monkeypatch.setattr(rss, "_fetch", lambda url, *a, **k: _VTT.encode())
    item = rss.FeedItem(
        title="Ep", guid="g", published=None,
        enclosure_url="https://x/e.mp3",
        transcript_url="https://x/e.vtt", transcript_type="text/vtt",
        duration=1800.0,
        description="Notes.\n0:00 Intro\n10:00 The middle\n20:00 The end",
    )
    source, _ = rss.ingest_podcast("https://x/feed.xml", item=item)
    assert source.chapters is not None
    assert [c["title"] for c in source.chapters] == ["Intro", "The middle", "The end"]
    assert source.chapters[0]["start"] == 0.0
    assert source.chapters[-1]["end"] == 1800.0  # last chapter closed at duration


def test_ingest_chapters_from_podcast_json_prefers_over_description(monkeypatch):
    def fake_fetch(url, *a, **k):
        if url.endswith(".json"):
            return _CHAPTERS_JSON.encode()
        return _VTT.encode()

    monkeypatch.setattr(rss, "_fetch", fake_fetch)
    item = rss.FeedItem(
        title="Ep", guid="g", published=None,
        enclosure_url="https://x/e.mp3",
        transcript_url="https://x/e.vtt", transcript_type="text/vtt",
        description="0:00 Intro\n10:00 Mid\n20:00 End",  # would parse, but...
        chapters_url="https://x/e-chapters.json",         # ...JSON wins
    )
    source, _ = rss.ingest_podcast("https://x/feed.xml", item=item)
    titles = [c["title"] for c in source.chapters]
    assert titles == ["Cold open", "Main topic", "Wrap"]
    assert source.chapters[1]["end"] == 600.0


def test_ingest_chapters_json_failure_falls_back_to_description(monkeypatch):
    def fake_fetch(url, *a, **k):
        if url.endswith(".json"):
            raise urllib.error.URLError("chapters host down")
        return _VTT.encode()

    monkeypatch.setattr(rss, "_fetch", fake_fetch)
    item = rss.FeedItem(
        title="Ep", guid="g", published=None,
        enclosure_url="https://x/e.mp3",
        transcript_url="https://x/e.vtt", transcript_type="text/vtt",
        description="0:00 Intro\n10:00 Mid\n20:00 End",
        chapters_url="https://x/e-chapters.json",
    )
    source, _ = rss.ingest_podcast("https://x/feed.xml", item=item)
    # chapters JSON blew up (retries exhausted) → description timestamps used.
    assert [c["title"] for c in source.chapters] == ["Intro", "Mid", "End"]


# --------------------------------------------------------------------------- #
# _fetch retry behaviour
# --------------------------------------------------------------------------- #

def test_fetch_retries_transient_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.URLError("temporary blip")
        return _FakeResp(b"payload")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(rss.time, "sleep", lambda *_: None)
    assert rss._fetch("https://x/feed.xml", backoff=0) == b"payload"
    assert calls["n"] == 3  # two failures, third succeeded


def test_fetch_retries_timeout(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("read timed out")
        return _FakeResp(b"ok")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(rss.time, "sleep", lambda *_: None)
    assert rss._fetch("https://x/feed.xml", backoff=0) == b"ok"
    assert calls["n"] == 2


def test_fetch_gives_up_after_retries(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.URLError("still down")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(rss.time, "sleep", lambda *_: None)
    with pytest.raises(urllib.error.URLError):
        rss._fetch("https://x/feed.xml", retries=2, backoff=0)
    assert calls["n"] == 3  # initial + 2 retries


def test_fetch_does_not_retry_4xx(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError("https://x/feed.xml", 404, "Not Found", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(rss.time, "sleep", lambda *_: None)
    with pytest.raises(urllib.error.HTTPError):
        rss._fetch("https://x/feed.xml", retries=2, backoff=0)
    assert calls["n"] == 1  # 404 is a real answer — no retry


def test_fetch_retries_5xx(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] < 2:
            raise urllib.error.HTTPError("https://x/feed.xml", 503, "Unavailable", {}, None)
        return _FakeResp(b"recovered")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(rss.time, "sleep", lambda *_: None)
    assert rss._fetch("https://x/feed.xml", backoff=0) == b"recovered"
    assert calls["n"] == 2
