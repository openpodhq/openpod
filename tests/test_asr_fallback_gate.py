import pytest

from openpod.asr import estimate_transcription
from openpod.errors import CaptionsUnavailable
from openpod.ingest import youtube as yt
from openpod.models import Cue, SourceRef, Transcript


def _tiny():
    return Transcript(cues=[Cue(start=0, end=5, text="hello world")],
                      source="asr:whisper-base")


def _setup(monkeypatch, *, failure, detail="detail", duration=3600.0):
    monkeypatch.setattr(yt, "_captions",
                        lambda vid, langs: (None, failure, detail))
    monkeypatch.setattr(yt, "_enrich_metadata",
                        lambda src: setattr(src, "duration", duration))
    monkeypatch.setattr("openpod.asr.download_audio", lambda url: "/tmp/x.wav")
    monkeypatch.setattr("openpod.asr.transcribe",
                        lambda path, **kw: _tiny())


URL = "https://www.youtube.com/watch?v=h1bZwuVCDZI"


# -- the gate ----------------------------------------------------------------- #

def test_rate_limited_long_asr_surfaces_the_choice(monkeypatch):
    # 1-hour episode: whisper is many minutes — waiting may well be faster.
    _setup(monkeypatch, failure="rate_limited", duration=3600)
    with pytest.raises(CaptionsUnavailable) as ei:
        yt.ingest_youtube(URL)
    d = ei.value.to_dict()
    assert d["error"] == "captions_unavailable"
    assert d["reason"] == "rate_limited"
    assert {o["option"] for o in d["options"]} == {"wait_and_recatch", "asr_now"}
    assert d["asr_estimate"]["human"]              # the user sees the cost


def test_rate_limited_short_asr_just_runs_with_notice(monkeypatch):
    # 10-minute episode: ~2 min of whisper — asking costs more than doing.
    _setup(monkeypatch, failure="rate_limited", duration=600)
    lines = []
    _source, t = yt.ingest_youtube(URL, progress=lines.append)
    assert t.source.startswith("asr:")
    assert "rate-limited" in t.notes
    assert lines and "no tokens" in lines[0]       # kills the cost worry


def test_unknown_duration_still_gates_transient(monkeypatch):
    # Can't price the run -> can't call it cheap -> surface the choice.
    _setup(monkeypatch, failure="transient", duration=None)
    with pytest.raises(CaptionsUnavailable):
        yt.ingest_youtube(URL)


def test_asr_now_proceeds_with_note_and_progress(monkeypatch):
    _setup(monkeypatch, failure="rate_limited", duration=3600)
    lines = []
    source, t = yt.ingest_youtube(URL, asr="now", progress=lines.append)
    assert t.source.startswith("asr:")
    assert "rate-limited" in t.notes
    assert "re-catch may upgrade" in t.notes       # the upgrade path is recorded
    assert lines and "transcribing locally" in lines[0]


def test_permanent_absence_auto_falls_through_but_notifies(monkeypatch):
    _setup(monkeypatch, failure="permanent")
    lines = []
    source, t = yt.ingest_youtube(URL, progress=lines.append)
    assert t.source.startswith("asr:")
    assert "no captions" in t.notes
    assert "upgrade" not in t.notes                # nothing to wait for
    assert lines and "no captions" in lines[0]


def test_asr_never_raises_even_for_permanent(monkeypatch):
    _setup(monkeypatch, failure="permanent")
    with pytest.raises(CaptionsUnavailable):
        yt.ingest_youtube(URL, asr="never")


def test_captions_success_untouched(monkeypatch):
    good = Transcript(cues=[Cue(start=0, end=2, text="hi")],
                      source="youtube-captions")
    monkeypatch.setattr(yt, "_captions", lambda vid, langs: (good, None, None))
    monkeypatch.setattr(yt, "_enrich_metadata", lambda src: None)
    _source, t = yt.ingest_youtube(URL)
    assert t.source == "youtube-captions" and t.notes is None


# -- failure classification ------------------------------------------------------ #

def test_classify_exceptions():
    class TranscriptsDisabled(Exception): ...
    class TooManyRequests(Exception): ...
    class WeirdError(Exception): ...

    assert yt._classify(TranscriptsDisabled("x"))[0] == "permanent"
    assert yt._classify(TooManyRequests("x"))[0] == "rate_limited"
    assert yt._classify(WeirdError("HTTP 429 from youtube"))[0] == "rate_limited"
    # unknown errors are retryable, never a silent 20-minute whisper run
    assert yt._classify(WeirdError("boom"))[0] == "transient"


# -- estimate ---------------------------------------------------------------------- #

def test_estimate_scales_with_duration_and_model():
    e = estimate_transcription(3600, "base")
    assert e["low_seconds"] < e["high_seconds"]
    assert "min" in e["human"]
    assert estimate_transcription(None)["human"].startswith("several")
    assert estimate_transcription(3600, "large")["high_seconds"] > e["high_seconds"]


# -- language preference reaches the caption fetch ---------------------------------- #

def test_caption_languages_follow_locale(monkeypatch, workspace):
    workspace.set_setting("locale.preferred_language", "he")
    seen = {}

    def fake_resolve_full(link, **kw):
        seen["languages"] = kw.get("caption_languages")
        from openpod.ingest.resolve import Resolution, origin_ref
        good = Transcript(cues=[Cue(start=0, end=2, text="שלום")],
                          source="youtube-captions", language="he")
        src = SourceRef(kind="youtube", url=link, video_id="x",
                        title="t", show="s")
        return Resolution(source=src, transcript=good, origin=origin_ref(link))

    import importlib

    resolve_mod = importlib.import_module("openpod.ingest.resolve")
    monkeypatch.setattr(resolve_mod, "resolve_full", fake_resolve_full)
    from openpod.catch import catch

    catch(URL, workspace=workspace)
    assert seen["languages"] == ["he", "en"]
