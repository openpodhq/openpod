import json

from openpod.captions import (FORCE_BREAK, captions_block, is_rtl,
                              phrases_for_window, split_phrases, to_srt,
                              captions_for_window, verify_coverage)
from openpod.catch import catch
from openpod.clip import clip, resolve_speakers, speaker_label
from openpod.models import Cue, SourceRef, Transcript


def _entry(workspace, vtt_file):
    return catch("https://example.com/ep1", workspace=workspace,
                 kind="podcast", transcript_path=str(vtt_file)).entry


# -- phrase chunking (the karaoke contract) ----------------------------------- #

def test_split_phrases_max_words():
    phrases = split_phrases("one two three four five six seven", max_words=5)
    assert [p for p, _ in phrases] == ["one two three four five", "six seven"]
    assert phrases[0][1] == "length"


def test_split_phrases_sentence_break():
    phrases = split_phrases("Short one. Then another sentence here.")
    assert phrases[0] == ("Short one.", "sentence")
    assert phrases[1][1] == "sentence"


def test_split_phrases_force_break_marker():
    # ‖ survives translation workflows: agents mark breaks, we never regroup.
    phrases = split_phrases(f"שלום עולם {FORCE_BREAK} עוד שורה")
    assert phrases[0] == ("שלום עולם", "force")
    assert phrases[1][0] == "עוד שורה"


def test_phrases_for_window_timing_monotonic(workspace, vtt_file):
    t = _entry(workspace, vtt_file).read_transcript()
    phrases = phrases_for_window(t, 0, 25)
    assert phrases and all(p["end"] > p["start"] for p in phrases)
    for a, b in zip(phrases, phrases[1:]):
        assert b["start"] >= a["start"]
    assert all(len(p["text"].split()) <= 5 for p in phrases)
    assert phrases[0]["start"] == 0.0            # clip-relative


def test_captions_block_honest_timing():
    yt = Transcript(cues=[Cue(start=0, end=4, text="rolling youtube cue here")],
                    source="youtube-captions", word_level=False)
    block = captions_block(yt, 0, 4)
    assert block["timing"] == "approximate"
    assert block["source"] == "transcript"
    pub = Transcript(cues=[Cue(start=0, end=4, text="publisher transcript")],
                     source="podcast:transcript")
    assert captions_block(pub, 0, 4)["timing"] == "cue"
    words = [{"text": "hi", "start": 0.0, "end": 0.2}]
    assert captions_block(pub, 0, 4, words=words)["timing"] == "exact"


# Rolling YouTube auto-captions: stored ends are when a line scrolls off
# screen, so cues overlap and a line lingers past the clip start. Same shape
# as ROLLING_VTT in test_clip_and_cli.py (the real Karpathy failure).
_ROLLING_CUES = [
    Cue(start=20.0, end=23.84, text="to this point a little bit uh later as"),
    Cue(start=21.76, end=25.2, text="well. And the last kind of feature I"),
    Cue(start=23.84, end=27.68, text="want to point out is that there's what I"),
    Cue(start=25.2, end=29.44, text="call the autonomy slider So for"),
    Cue(start=27.68, end=31.52, text="example in cursor you can just do tap"),
    Cue(start=29.44, end=33.6, text="completion. You're mostly in charge."),
]


def test_captions_rolling_cues_exclude_lingering_line():
    t = Transcript(cues=_ROLLING_CUES, source="youtube-captions")
    lines = captions_for_window(t, 21.76, 33.6)
    # the 20.0 cue is still on screen at 21.76 but its words are already
    # spoken — it must not show text at clip start that isn't in the audio
    assert lines[0].text.startswith("well. And the last")
    assert all("to this point" not in l.text for l in lines)
    assert lines[0].start == 0.0
    # spoken spans: sidecar lines don't overlap the way raw rolling ends do
    for a, b in zip(lines, lines[1:]):
        assert b.start >= a.end


def test_verify_coverage_over_audible_cues_only():
    t = Transcript(cues=_ROLLING_CUES, source="youtube-captions")
    lines = captions_for_window(t, 21.76, 33.6)
    report = verify_coverage(t, 21.76, 33.6, lines)
    assert report.total_cues == 5      # not 6: the pre-cut line isn't owed
    assert report.ok
    # dropping a line is still a failed check, never a silent gap
    assert not verify_coverage(t, 21.76, 33.6, lines[:-1]).ok


def test_is_rtl():
    assert is_rtl("he") and is_rtl("ar") and is_rtl("he-IL")
    assert not is_rtl("en") and not is_rtl(None)


# -- clip json carries the caption track --------------------------------------- #

def test_clip_json_embeds_caption_track(workspace, vtt_file, tiny_wav_file):
    entry = _entry(workspace, vtt_file)
    r = clip(entry.entry_id, 5, 20, workspace=workspace,
             audio_path=str(tiny_wav_file), captions="soft",
             hook="LPU vs GPU in one line")
    meta_path = next(p for p in entry.clips_dir.glob("*.json")
                     if not p.name.endswith("card.json"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["captions"]["phrases"]
    assert meta["captions"]["timing"] in ("cue", "approximate")
    assert "rtl" in meta["captions"]
    assert meta["hook"] == "LPU vs GPU in one line"


# -- speakers: title alias + speakers.yaml fallback ------------------------------ #

def test_speaker_label_title_alias():
    src = SourceRef(kind="podcast", speakers=[
        {"name": "Jonathan Ross", "title": "Founder, Groq", "primary": True}])
    assert speaker_label(src, "{name}, {title}") == "Jonathan Ross, Founder, Groq"
    assert speaker_label(src, "{name}, {role}") == "Jonathan Ross, Founder, Groq"


def test_resolve_speakers_from_workspace_yaml(workspace):
    (workspace.dot / "speakers.yaml").write_text(
        "David Senra: [{name: David Senra, title: Host, primary: true}]\n",
        encoding="utf-8")
    src = SourceRef(kind="podcast", show="David Senra")
    speakers = resolve_speakers(src, workspace)
    assert speakers and speakers[0]["name"] == "David Senra"
    # source's own speakers win over the yaml
    src2 = SourceRef(kind="podcast", show="David Senra",
                     speakers=[{"name": "Guest"}])
    assert resolve_speakers(src2, workspace)[0]["name"] == "Guest"


# -- export package + burn gate --------------------------------------------------- #

def test_export_package_contents(workspace, vtt_file, tiny_wav_file, tmp_path):
    entry = _entry(workspace, vtt_file)
    out = tmp_path / "exports"
    r = clip(entry.entry_id, 5, 20, workspace=workspace,
             audio_path=str(tiny_wav_file), captions="soft",
             label_text="Jonathan Ross, Founder, Groq",
             hook="the hook", out_dir=str(out))
    names = {p.name for p in r.export_paths}
    assert "deeplink.txt" not in names or (out / "deeplink.txt").exists()
    assert "label.json" in names
    label = json.loads((out / "label.json").read_text(encoding="utf-8"))
    assert label == {"label": "Jonathan Ross, Founder, Groq", "hook": "the hook"}


def test_burn_refuses_unverified_translation(workspace, vtt_file,
                                             tiny_wav_file, tmp_path):
    workspace.set_setting("locale.preferred_language", "he")
    entry = _entry(workspace, vtt_file)
    # Known source language that differs from the preference -> refusal.
    tr = entry.read_transcript()
    tr.language = "en"
    entry.write_transcript(tr)
    r = clip(entry.entry_id, 5, 20, workspace=workspace,
             audio_path=str(tiny_wav_file), captions="burn",
             out_dir=str(tmp_path / "x"))
    # translation needed + no verified file -> refusal note, no social file
    assert "refusing to burn" in r.capability_note
    assert not any("social" in p.name for p in r.export_paths)


def test_burn_proceeds_when_language_unlabeled(workspace, vtt_file,
                                               tiny_wav_file, tmp_path):
    # Unknown source language: can't prove a mismatch — burn, but tell the
    # user to check the frames (the gate only blocks proven mismatches).
    workspace.set_setting("locale.preferred_language", "he")
    entry = _entry(workspace, vtt_file)
    r = clip(entry.entry_id, 5, 20, workspace=workspace,
             audio_path=str(tiny_wav_file), captions="burn",
             out_dir=str(tmp_path / "x"))
    assert "refusing to burn" not in (r.capability_note or "")
    assert "unlabeled" in r.capability_note


def test_burn_gate_rejects_low_coverage_file(workspace, vtt_file,
                                             tiny_wav_file, tmp_path):
    entry = _entry(workspace, vtt_file)
    t = entry.read_transcript()
    lines = captions_for_window(t, 5, 20)
    partial = to_srt(lines[:1])                   # dropped most of the speech
    bad = tmp_path / "bad.he.srt"
    bad.write_text(partial, encoding="utf-8")
    r = clip(entry.entry_id, 5, 20, workspace=workspace,
             audio_path=str(tiny_wav_file), captions="burn",
             captions_file=str(bad), out_dir=str(tmp_path / "x"))
    assert "refusing to burn" in r.capability_note
    assert "missing" in r.capability_note


def test_append_note_lead_puts_critical_first():
    from openpod.clip import _append_note
    assert _append_note(None, "A", lead=True) == "A"
    assert _append_note("B", "A") == "B; A"             # default: appends
    assert _append_note("B", "A", lead=True) == "A; B"  # lead: prepends


def test_missing_libass_warning_leads_and_is_actionable(monkeypatch):
    """No libass -> captions can't burn. The degrade must LEAD the
    capability_note (not hide behind minor notes) and point to the fix."""
    from pathlib import Path
    import openpod.asr as asr
    from openpod.clip import ClipResult, _burn

    monkeypatch.setattr(asr, "ffmpeg_capabilities",
                        lambda: {"ffmpeg": True, "subtitles": False,
                                 "drawtext": False})

    r = ClipResult(path=Path("/tmp/master.mp4"), start=0.0, end=6.0,
                   quote="q", deeplink=None, has_video=True)
    r.capability_note = "sidecar written line-by-line"   # a pre-existing minor note

    out = _burn(r, captions_path="/tmp/x.srt", label=None, dest=Path("/tmp"))

    assert out is None                                    # nothing burned
    assert r.capability_note.startswith("CAPTIONS NOT BURNED")   # it leads
    assert "libass" in r.capability_note
    assert "README install section" in r.capability_note
    assert "openpod doctor" in r.capability_note
    assert "sidecar written line-by-line" in r.capability_note   # prior note kept
