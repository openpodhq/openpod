import textwrap

import openpod.transcript as tx
from openpod.clip import snap_to_cues
from openpod.cli import main


def test_snap_to_cues_expands_to_boundaries(vtt_file):
    t = tx.load_file(vtt_file)
    # request a span inside cue 2 (5–11) to inside cue 3 (11–18)
    start, end = snap_to_cues(t, 7.0, 13.0)
    assert start == 5.0        # snapped down to cue-2 start
    assert end >= 18.0         # snapped up to cue-3 end


# YouTube-style rolling captions: each line's stored end is when it scrolls
# off screen, so cues overlap and cue edges are line-wraps mid-sentence.
# Modeled on the real failure (Karpathy, ~1223s): the sentence "And the last
# kind of feature…" starts *inside* the 21.76 cue.
ROLLING_VTT = textwrap.dedent(
    """\
    WEBVTT

    00:00:20.000 --> 00:00:23.840
    to this point a little bit uh later as

    00:00:21.760 --> 00:00:25.200
    well. And the last kind of feature I

    00:00:23.840 --> 00:00:27.680
    want to point out is that there's what I

    00:00:25.200 --> 00:00:29.440
    call the autonomy slider So for

    00:00:27.680 --> 00:00:31.520
    example in cursor you can just do tap

    00:00:29.440 --> 00:00:33.600
    completion. You're mostly in charge.
    """
)


def test_snap_rolling_captions_finds_sentence_start():
    t = tx.load(ROLLING_VTT, fmt="vtt")
    start, end = snap_to_cues(t, 24.0, 28.0)
    # NOT the mid-sentence containing cue (23.84 "want to point out…") —
    # the cue where the sentence actually starts ("well. And the last…").
    assert start == 21.76
    # end extends to the cue that closes a sentence, cut at its spoken end
    assert end == 33.6


def test_quote_excludes_lingering_rolling_cue():
    t = tx.load(ROLLING_VTT, fmt="vtt")
    texts = [c.text for c in t.spoken_window(21.76, 33.6)]
    # the 20.0 cue is still on screen at 21.76 but its words are already
    # spoken — it must not leak into the quote
    assert texts[0].startswith("well. And the last")
    assert all("to this point" not in txt for txt in texts)


# Unpunctuated auto-captions (older videos): no sentence punctuation at all,
# so the snap must fall back to spoken pauses in the cue timing.
UNPUNCTUATED_VTT = textwrap.dedent(
    """\
    WEBVTT

    00:00:00.000 --> 00:00:04.000
    so the first thing we looked at was the

    00:00:02.000 --> 00:00:06.000
    training data and how it was collected

    00:00:07.000 --> 00:00:11.000
    the second thing was the model

    00:00:09.000 --> 00:00:13.000
    architecture and its layers
    """
)


def test_snap_unpunctuated_captions_uses_pause():
    t = tx.load(UNPUNCTUATED_VTT, fmt="vtt")
    start, _ = snap_to_cues(t, 9.5, 12.0)
    # no punctuation anywhere: snap to the 1s silence gap before 7.0,
    # not to the mid-phrase containing cue at 9.0
    assert start == 7.0


def test_snap_avoids_filler_word_open():
    # continuous rolling, no punctuation, no pauses: last-resort cue-edge
    # snapping must not open the clip cold on "uh"
    cues = "\n\n".join(
        f"00:00:{s:02d}.000 --> 00:00:{s + 4:02d}.000\n{txt}"
        for s, txt in [
            (0, "we spent a lot of time thinking about"),
            (2, "the evaluation setup and the metrics"),
            (4, "that we used for the ranking model"),
            (6, "and the way that we sampled the data"),
            (8, "from the production traffic that day"),
            (10, "which turned out to matter a lot for"),
            (12, "the quality of the final judgments"),
            (14, "uh the second area we invested in was"),
        ]
    )
    t = tx.load("WEBVTT\n\n" + cues + "\n", fmt="vtt")
    start, _ = snap_to_cues(t, 14.6, 17.0)
    assert start == 12.0


def test_cli_catch_search_list(workspace, vtt_file, capsys, monkeypatch):
    monkeypatch.setenv("OPENPOD_HOME", str(workspace.root))

    assert main(["catch", "https://example.com/ep1", "--kind", "podcast",
                 "--transcript", str(vtt_file)]) == 0
    out = capsys.readouterr().out
    assert "caught:" in out

    assert main(["search", "consensus"]) == 0
    out = capsys.readouterr().out
    assert "consensus" in out.lower()

    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "/" in out  # entry id show/episode


def test_cli_version(capsys):
    assert main(["version"]) == 0
    assert "openpod" in capsys.readouterr().out


def test_cli_unknown_entry_errors(workspace, monkeypatch, capsys):
    monkeypatch.setenv("OPENPOD_HOME", str(workspace.root))
    rc = main(["export-timestamps", "no/such"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err
