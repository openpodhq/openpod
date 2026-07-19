"""Caption styles — the ASS burn path (keyword / marker / karaoke).

The defining capability of every named style is a differently-colored word,
which a plain SRT + uniform force_style cannot express. The burn now writes
an .ass whose style block mirrors the old force_style defaults exactly, and
whose events carry inline override tags: agent-marked `*word*` keywords
(the ‖ contract extended — chosen per line, never guessed) and `{\\k}`
karaoke timing consumed from the word-level track.
"""

from pathlib import Path

import pytest

from openpod.ass import build_ass, hex_to_ass, parse_keywords
from openpod.catch import catch
from openpod.clip import clip

STYLE = {"font": None, "color": "#FFFFFF", "outline": "#000000",
         "boxed": True, "position": "bottom",
         "keyword_color": "#58A6FF", "weight": None, "shadow": None}


# -- keyword marks ----------------------------------------------------------- #

def test_parse_keywords_single_and_multiword():
    clean, spans = parse_keywords("the *dopamine* baseline")
    assert clean == "the dopamine baseline"
    assert [clean[s:e] for s, e in spans] == ["dopamine"]

    clean, spans = parse_keywords("mark *two words* here")
    assert clean == "mark two words here"
    assert [clean[s:e] for s, e in spans] == ["two words"]


def test_parse_keywords_multiple_and_none():
    clean, spans = parse_keywords("*first* then *second*")
    assert clean == "first then second"
    assert [clean[s:e] for s, e in spans] == ["first", "second"]
    assert parse_keywords("no marks at all") == ("no marks at all", [])


def test_parse_keywords_rtl():
    # Hebrew line, agent-marked keyword — chosen per line, never heuristic.
    clean, spans = parse_keywords("הרגע שבו *הכול* השתנה")
    assert clean == "הרגע שבו הכול השתנה"
    assert [clean[s:e] for s, e in spans] == ["הכול"]


def test_hex_to_ass_reverses_bytes():
    assert hex_to_ass("#58A6FF") == "&H00FFA658"
    assert hex_to_ass("#000000", alpha="60") == "&H60000000"


# -- the document ------------------------------------------------------------ #

def test_plain_style_mirrors_force_style_defaults():
    ass = build_ass([(0.0, 2.5, "hello there")], style=STYLE, mode="plain")
    # the old force_style vocabulary, now in the Style line
    assert "&H00FFFFFF" in ass          # PrimaryColour white
    assert "&H60000000" in ass          # boxed outline alpha
    style_line = next(l for l in ass.splitlines() if l.startswith("Style:"))
    fields = style_line.split(",")
    assert fields[15] == "4"            # BorderStyle=4 (boxed)
    assert fields[18] == "2"            # Alignment=2 (bottom)
    assert fields[21] == "40"           # MarginV=40
    assert "Dialogue: 0,0:00:00.00,0:00:02.50,OpenPod,,0,0,0,,hello there" in ass


def test_unboxed_top_and_bold_shadow_knobs():
    style = {**STYLE, "boxed": False, "position": "top",
             "weight": 700, "shadow": 2}
    ass = build_ass([(0.0, 1.0, "x")], style=style, mode="plain")
    style_line = next(l for l in ass.splitlines() if l.startswith("Style:"))
    fields = style_line.split(",")
    assert fields[15] == "1"            # BorderStyle=1 (outline)
    assert fields[18] == "8"            # Alignment=8 (top)
    assert fields[7] == "-1"            # Bold
    assert fields[17] == "2"            # Shadow depth


def test_keyword_mode_colors_the_marked_word():
    ass = build_ass([(0.0, 2.0, "the *dopamine* baseline")], style=STYLE,
                    mode="keyword")
    assert "{\\c&H00FFA658&}" not in ass  # colors carry no trailing & here
    assert "{\\c&H00FFA658}dopamine{\\c&H00FFFFFF}" in ass
    assert "*" not in ass.split("[Events]")[1]   # marks never burn literally


def test_marker_is_the_same_rendering():
    kw = build_ass([(0.0, 2.0, "*x* y")], style=STYLE, mode="keyword")
    mk = build_ass([(0.0, 2.0, "*x* y")], style=STYLE, mode="marker")
    assert kw.split("[Events]")[1] == mk.split("[Events]")[1]


def test_force_break_becomes_hard_line_break():
    ass = build_ass([(0.0, 2.0, "שורה ראשונה ‖ שורה שנייה")], style=STYLE,
                    mode="plain")
    assert "\\N" in ass
    assert "‖" not in ass.split("[Events]")[1]


def test_karaoke_consumes_word_timings():
    words = [{"text": "the", "start": 0.0, "end": 0.3},
             {"text": "moment", "start": 0.5, "end": 1.1}]
    ass = build_ass([(0.0, 2.0, "the moment")], style=STYLE, mode="karaoke",
                    words=words)
    style_line = next(l for l in ass.splitlines() if l.startswith("Style:"))
    fields = style_line.split(",")
    assert fields[3] == "&H00FFA658"    # Primary = sung = keyword blue
    assert fields[4] == "&H00FFFFFF"    # Secondary = unsung = caption color
    assert "{\\k30}the" in ass          # 0.3s -> 30cs
    assert "{\\k20}" in ass             # the 0.2s gap before "moment"
    assert "{\\k60}moment" in ass


def test_karaoke_uncovered_line_and_no_words_degrade():
    words = [{"text": "the", "start": 0.0, "end": 0.3}]
    ass = build_ass([(0.0, 1.0, "the"), (5.0, 6.0, "uncovered line")],
                    style=STYLE, mode="karaoke", words=words)
    # a line the track doesn't cover renders uniformly in the base color
    assert "{\\c&H00FFFFFF}uncovered line" in ass
    # no words at all -> keyword rendering (caller notes the degradation)
    no_words = build_ass([(0.0, 1.0, "*x* y")], style=STYLE, mode="karaoke")
    assert "{\\c&H00FFA658}x{\\c&H00FFFFFF}" in no_words


def test_deterministic_and_brace_safe():
    lines = [(0.0, 2.0, "a {brace} *k*")]
    assert build_ass(lines, style=STYLE, mode="keyword") == \
        build_ass(lines, style=STYLE, mode="keyword")
    assert "\\{brace\\}" in build_ass(lines, style=STYLE, mode="keyword")


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        build_ass([], style=STYLE, mode="disco")


# -- wiring ------------------------------------------------------------------ #

def _fake_ffmpeg(monkeypatch, tmp_path):
    import openpod.asr as asr
    import openpod.clip as clip_mod

    monkeypatch.setattr(asr, "ffmpeg_capabilities",
                        lambda: {"subtitles": True, "drawtext": True})

    real_run = clip_mod.subprocess.run

    def fake_run(cmd, **kw):
        if cmd and cmd[0] == "ffmpeg":
            out = Path(cmd[-1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"fake")

            class R:
                returncode = 0
                stderr = ""
            return R()
        return real_run(cmd, **kw)

    monkeypatch.setattr(clip_mod.subprocess, "run", fake_run)


def test_burn_writes_ass_with_keyword_overrides(workspace, vtt_file,
                                                tmp_path, monkeypatch):
    _fake_ffmpeg(monkeypatch, tmp_path)
    entry = catch("https://example.com/ep1", workspace=workspace,
                  kind="podcast", transcript_path=str(vtt_file)).entry
    marked = tmp_path / "marked.srt"
    marked.write_text(
        "1\n00:00:00,000 --> 00:00:06,000\n"
        "Today we talk about *consensus* algorithms like Raft and Paxos.\n",
        encoding="utf-8")
    # a video container so the burn path engages
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    r = clip(entry.entry_id, 5, 11, workspace=workspace,
             audio_path=str(video), captions="burn",
             captions_file=str(marked), style="marker",
             out_dir=str(tmp_path / "out"))
    assert r.style == "marker"
    ass_files = [p for p in r.export_paths if str(p).endswith(".ass")]
    assert len(ass_files) == 1
    content = Path(ass_files[0]).read_text(encoding="utf-8")
    assert "{\\c&H00FFA658}consensus{\\c&H00FFFFFF}" in content


def test_invalid_style_rejected(workspace, vtt_file, tiny_wav_file):
    entry = catch("https://example.com/ep1", workspace=workspace,
                  kind="podcast", transcript_path=str(vtt_file)).entry
    with pytest.raises(ValueError):
        clip(entry.entry_id, 5, 11, workspace=workspace,
             audio_path=str(tiny_wav_file), style="disco")


# -- RTL: base direction + font (all users, not just one clip) --------------- #

def test_rtl_pins_each_visual_line_to_rtl_base():
    """A line opening with a Latin word must not flip. Each \\N segment is
    wrapped in RLE…PDF so the base direction is RTL and Latin/number runs
    stay as correct LTR islands."""
    from openpod.ass import _RLE, _PDF
    from openpod.captions import FORCE_BREAK
    lines = [(0.0, 3.0, "Claude Max נותן גישה"),
             (3.0, 6.0, f"יש API {FORCE_BREAK} וגם STEM")]
    doc = build_ass(lines, style=STYLE, mode="plain", rtl=True)
    texts = [ln.split(",", 9)[-1] for ln in doc.splitlines()
             if ln.startswith("Dialogue")]
    # line 1: single visual segment, wrapped exactly once
    assert texts[0].startswith(_RLE) and texts[0].endswith(_PDF)
    assert texts[0].count(_RLE) == 1
    # line 2: forced break -> two segments, EACH independently wrapped
    segs = texts[1].split(r"\N")
    assert len(segs) == 2
    assert all(s.startswith(_RLE) and s.endswith(_PDF) for s in segs)
    # LTR from the same input carries zero marks (byte-for-byte no regression)
    ltr = build_ass(lines, style=STYLE, mode="plain", rtl=False)
    assert _RLE not in ltr and _PDF not in ltr


def test_rtl_wrap_keeps_keyword_override_tags():
    """The bidi marks sit on the outside; inline {\\c} keyword overrides stay
    intact inside the wrapped run."""
    from openpod.ass import _RLE, _PDF
    doc = build_ass([(0.0, 3.0, "מודל *Claude* חדש")], style=STYLE,
                    mode="keyword", rtl=True)
    text = [ln.split(",", 9)[-1] for ln in doc.splitlines()
            if ln.startswith("Dialogue")][0]
    assert text.startswith(_RLE) and text.endswith(_PDF)
    assert "\\c" in text                       # keyword color override preserved


def test_rtl_default_font_is_scoped_to_rtl():
    from openpod.ass import _RTL_FONT
    head = lambda doc: doc.split("[Events]")[0]
    # RTL + unset font -> RTL default; LTR + unset font -> Arial (unchanged)
    assert f"Style: OpenPod,{_RTL_FONT}," in head(
        build_ass([(0.0, 1.0, "שלום")], style=STYLE, rtl=True))
    assert "Style: OpenPod,Arial," in head(
        build_ass([(0.0, 1.0, "hello")], style=STYLE, rtl=False))
    # an explicit caption_style.font wins even in RTL
    assert "Style: OpenPod,Heebo," in head(
        build_ass([(0.0, 1.0, "שלום")], style={**STYLE, "font": "Heebo"}, rtl=True))


def test_clip_burn_threads_rtl_through_for_hebrew(workspace, vtt_file,
                                                  tmp_path, monkeypatch):
    """End-to-end: a Hebrew-labelled caption makes clip() write an RTL .ass
    (bidi marks + RTL font) and note the forced direction."""
    from openpod.ass import _RLE, _RTL_FONT
    _fake_ffmpeg(monkeypatch, tmp_path)
    workspace.set_setting("locale.preferred_language", "he")
    entry = catch("https://example.com/ep1", workspace=workspace,
                  kind="podcast", transcript_path=str(vtt_file)).entry
    tr = entry.read_transcript(); tr.language = "he"; entry.write_transcript(tr)
    video = tmp_path / "clip.mp4"; video.write_bytes(b"fake")   # engage burn path
    r = clip(entry.entry_id, 5, 20, workspace=workspace,
             audio_path=str(video), captions="burn",
             out_dir=str(tmp_path / "ex"))
    ass = next(p for p in r.export_paths if p.suffix == ".ass")
    doc = ass.read_text(encoding="utf-8")
    assert _RLE in doc                              # base direction forced
    assert f"Style: OpenPod,{_RTL_FONT}," in doc    # RTL default font applied
    assert "forced right-to-left" in (r.capability_note or "")
