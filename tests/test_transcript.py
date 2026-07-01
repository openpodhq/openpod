from openpod import transcript as tx
from openpod.transcript import parse_timestamp


def test_parse_timestamp_forms():
    assert parse_timestamp("00:00:05.000") == 5.0
    assert parse_timestamp("01:02:03.500") == 3723.5
    assert parse_timestamp("02:30") == 150.0
    assert parse_timestamp("00:00:01,250") == 1.25  # SRT comma


def test_load_vtt(vtt_file):
    t = tx.load_file(vtt_file)
    assert len(t) == 5
    assert t.cues[0].start == 0.0
    assert t.cues[0].end == 5.0
    assert "distributed systems" in t.cues[0].text
    assert t.duration == 66.0


def test_json3():
    doc = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "hello "}, {"utf8": "world"}]},
            {"tStartMs": 2000, "dDurationMs": 1000, "segs": [{"utf8": "again"}]},
        ]
    }
    cues = tx.parse_json3(doc)
    assert [c.text for c in cues] == ["hello world", "again"]
    assert cues[1].start == 2.0


def test_window_and_cue_at(vtt_file):
    t = tx.load_file(vtt_file)
    win = t.window(4, 12)
    assert any("consensus" in c.text.lower() for c in win)
    assert t.cue_at(6).start == 5.0


def test_dedupe_rolling_captions():
    doc = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "same line"}]},
            {"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "same line"}]},
        ]
    }
    assert len(tx.parse_json3(doc)) == 1
