"""The podcast-path twin of test_asr_fallback_gate.py.

The ASR control surface (asr= / auto_threshold / progress) must hold when a
feed ships no transcript, not only when YouTube captions fail — a `--no-asr`
catch of an Apple link must refuse *before* the enclosure download, never
grind minutes of silent Whisper.
"""

import pytest

from openpod.errors import TranscriptUnavailable
from openpod.ingest import rss
from openpod.models import Cue, SourceRef, Transcript


def _tiny():
    return Transcript(cues=[Cue(start=0, end=5, text="hello world")],
                      source="asr:whisper-base")


class _Media:
    url = "https://cdn.example/ep.mp3"


def _item(*, duration=3600.0, transcript_url=None, transcript_type=None):
    return rss.FeedItem(
        title="Ep 1", guid="g1", published="Mon, 01 Jan 2026 00:00:00 GMT",
        enclosure_url="https://cdn.example/ep.mp3",
        transcript_url=transcript_url, transcript_type=transcript_type,
        duration=duration)


def _setup(monkeypatch, *, touched=None):
    """Stub the network/compute tail; ``touched`` records any download."""
    def _ensure(url, **kw):
        if touched is not None:
            touched.append(("validate", url))
        return _Media()

    def _download(url, **kw):
        if touched is not None:
            touched.append(("download", url))
        return "/tmp/x.wav"

    monkeypatch.setattr("openpod.ingest.validate.ensure_media", _ensure)
    monkeypatch.setattr("openpod.asr.download_audio", _download)
    monkeypatch.setattr("openpod.asr.transcribe", lambda path, **kw: _tiny())


FEED = "https://feeds.example/pod.xml"


# -- the gate ----------------------------------------------------------------- #

def test_no_transcript_long_asr_surfaces_the_choice(monkeypatch):
    # 1-hour episode: whisper is many minutes — the route is the user's call.
    _setup(monkeypatch)
    with pytest.raises(TranscriptUnavailable) as ei:
        rss.ingest_podcast(FEED, item=_item(duration=3600), feed_title="Show")
    d = ei.value.to_dict()
    assert d["error"] == "transcript_unavailable"
    assert {o["option"] for o in d["options"]} == {"asr_now", "alternate_source"}
    assert d["asr_estimate"]["human"]              # the user sees the cost
    assert d["title"] == "Ep 1"


def test_no_transcript_short_asr_just_runs_with_notice(monkeypatch):
    # 10-minute episode: ~2 min of whisper — asking costs more than doing.
    _setup(monkeypatch)
    lines = []
    _source, t = rss.ingest_podcast(FEED, item=_item(duration=600),
                                    progress=lines.append)
    assert t.source.startswith("asr:")
    assert "no publisher transcript" in t.notes
    assert lines and "no tokens" in lines[0]       # kills the cost worry


def test_unknown_duration_still_gates(monkeypatch):
    # Can't price the run -> can't call it cheap -> surface the choice.
    _setup(monkeypatch)
    with pytest.raises(TranscriptUnavailable):
        rss.ingest_podcast(FEED, item=_item(duration=None))


def test_asr_never_refuses_before_any_download(monkeypatch):
    # Even a cheap episode: "never" means never, and the refusal is free —
    # nothing validated, nothing downloaded.
    touched = []
    _setup(monkeypatch, touched=touched)
    with pytest.raises(TranscriptUnavailable):
        rss.ingest_podcast(FEED, item=_item(duration=600), asr="never")
    assert touched == []


def test_asr_now_proceeds_with_progress(monkeypatch):
    _setup(monkeypatch)
    lines = []
    _source, t = rss.ingest_podcast(FEED, item=_item(duration=3600),
                                    asr="now", progress=lines.append)
    assert t.source.startswith("asr:")
    assert lines and "transcribing locally" in lines[0]


def test_publisher_transcript_untouched_by_gate(monkeypatch):
    # A timed feed transcript short-circuits everything — asr="never" is
    # happily satisfied without ASR ever being considered.
    vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nhi\n"
    monkeypatch.setattr(rss, "_fetch", lambda url, **kw: vtt.encode())
    item = _item(duration=3600,
                 transcript_url="https://x/t.vtt", transcript_type="text/vtt")
    _source, t = rss.ingest_podcast(FEED, item=item, asr="never")
    assert t.source == "podcast:transcript"


def test_bad_asr_value_rejected():
    with pytest.raises(ValueError):
        rss.ingest_podcast(FEED, item=_item(), asr="no")


# -- the control surface reaches the podcast paths --------------------------- #

def test_resolve_full_threads_asr_to_podcast_path(monkeypatch):
    import importlib

    rmod = importlib.import_module("openpod.ingest.resolve")
    seen = {}

    def fake_ingest(link, **kw):
        seen.update(kw)
        return SourceRef(kind="podcast", url=link), _tiny()

    monkeypatch.setattr(rss, "ingest_podcast", fake_ingest)
    rmod.resolve_full(FEED, asr="never", asr_auto_threshold=42.0)
    assert seen["asr"] == "never"
    assert seen["auto_threshold"] == 42.0


def test_resolve_platform_threads_asr_too(monkeypatch):
    # The live repro was an Apple link — the platform path must thread the
    # same surface after the identity/confirmation gate.
    import importlib

    rmod = importlib.import_module("openpod.ingest.resolve")
    from openpod.models import EpisodeIdentity

    item = _item(duration=3600)
    ident = EpisodeIdentity(feed_url=FEED, rss_guid="g1",
                            show="Show", title="Ep 1")
    feed = rss.Feed(title="Show", items=[item])
    monkeypatch.setattr("openpod.ingest.apple.prepare_apple",
                        lambda url: (ident, feed, item, 1.0, "feed-guid"))

    seen = {}

    def fake_ingest(link, **kw):
        seen.update(kw)
        return SourceRef(kind="podcast", url=link), _tiny()

    monkeypatch.setattr(rss, "ingest_podcast", fake_ingest)
    rmod.resolve_full("https://podcasts.apple.com/us/podcast/x/id1?i=2",
                      asr="never", asr_auto_threshold=42.0)
    assert seen["asr"] == "never"
    assert seen["auto_threshold"] == 42.0
