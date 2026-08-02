import pytest

from openpod.captions import (captions_for_window, export_captions, parse_srt,
                              to_srt, to_vtt, verify_coverage)
from openpod.catch import catch
from openpod.clip import speaker_label
from openpod.config import DEFAULT_SETTINGS
from openpod.doctor import check
from openpod.models import SourceRef


def _entry(workspace, vtt_file):
    # These tests exercise caption/settings mechanics on a configured
    # workspace; the needs_decision gate is tested in test_clip_setup.py.
    workspace.set_setting("clip.setup_done", True)
    return catch("https://example.com/ep1", workspace=workspace,
                 kind="podcast", transcript_path=str(vtt_file)).entry


# -- settings schema ------------------------------------------------------- #

def test_effective_settings_defaults(workspace):
    s = workspace.effective_settings()
    assert s["clip"]["captions"] == "off"
    assert s["clip"]["burn_in"] is False          # the load-bearing default
    assert s["clip"]["keep_clean_master"] is True
    assert s["locale"]["fallback_language"] == "en"


def test_set_get_dotted_keys(workspace):
    workspace.set_setting("locale.preferred_language", "he")
    workspace.set_setting("clip.captions", "soft")
    assert workspace.get_setting("locale.preferred_language") == "he"
    assert workspace.get_setting("clip.captions") == "soft"
    # user file holds only overrides; effective merges defaults back in
    assert workspace.load_settings() == {
        "locale": {"preferred_language": "he"}, "clip": {"captions": "soft"}}
    assert workspace.effective_settings()["clip"]["burn_in"] is False


def test_unknown_setting_rejected(workspace):
    with pytest.raises(KeyError) as ei:
        workspace.set_setting("clip.captoins", "on")
    assert "clip.captions" in str(ei.value)       # typo help lists real keys


# -- captions -------------------------------------------------------------- #

def test_captions_full_coverage_by_construction(workspace, vtt_file):
    entry = _entry(workspace, vtt_file)
    t = entry.read_transcript()
    lines = captions_for_window(t, 5, 25)
    report = verify_coverage(t, 5, 25, lines)
    assert report.ok and report.ratio == 1.0
    assert lines[0].start == 0.0                  # clip-relative clock


def test_verify_catches_dropped_line(workspace, vtt_file):
    # The QA-04 case: a "summarized" caption set that silently lost a beat.
    entry = _entry(workspace, vtt_file)
    t = entry.read_transcript()
    lines = captions_for_window(t, 0, 25)
    dropped = lines[:1] + lines[2:]               # drop the second beat
    report = verify_coverage(t, 0, 25, dropped)
    assert not report.ok
    assert len(report.gaps) == 1
    assert "consensus" in report.gaps[0]["text"].lower()


def test_export_captions_translation_flag(workspace, vtt_file):
    workspace.set_setting("locale.preferred_language", "he")
    entry = _entry(workspace, vtt_file)
    r = export_captions(entry, 0, 25)
    assert r.path.exists() and r.path.suffix == ".srt"
    assert r.requested_language == "he"
    assert r.translation_needed                   # transcript is en
    assert r.coverage.ok


def test_srt_roundtrip(workspace, vtt_file):
    entry = _entry(workspace, vtt_file)
    t = entry.read_transcript()
    lines = captions_for_window(t, 0, 25)
    parsed = parse_srt(to_srt(lines))
    assert len(parsed) == len(lines)
    assert parsed[0].text == lines[0].text
    assert abs(parsed[-1].end - lines[-1].end) < 0.002
    assert to_vtt(lines).startswith("WEBVTT")


def test_to_srt_rtl_wraps_each_visual_line():
    from openpod.captions import CaptionLine, _RLE, _PDF
    lines = [CaptionLine(start=0.0, end=2.0, text="API חדש"),
             CaptionLine(start=2.0, end=4.0, text="שורה\nעם המשך")]
    body = [l for l in to_srt(lines, rtl=True).splitlines()
            if l and "-->" not in l and not l.isdigit()]
    assert len(body) == 3                              # cue1 (1 line) + cue2 (2)
    assert all(l.startswith(_RLE) and l.endswith(_PDF) for l in body)
    assert _RLE in to_vtt(lines, rtl=True)
    assert _RLE not in to_srt(lines)                   # default LTR: no marks


def test_export_captions_marks_rtl_source_sidecar(workspace, vtt_file):
    from openpod.captions import _RLE
    entry = _entry(workspace, vtt_file)
    tr = entry.read_transcript(); tr.language = "he"; entry.write_transcript(tr)
    r = export_captions(entry, 0, 25)
    assert r.language == "he"
    assert _RLE in r.path.read_text(encoding="utf-8")  # sidecar pins RTL base
    # an English-source sidecar stays mark-free (no regression)
    tr.language = "en"; entry.write_transcript(tr)
    r2 = export_captions(entry, 0, 25)
    assert _RLE not in r2.path.read_text(encoding="utf-8")


# -- speaker label from structure ------------------------------------------- #

def test_speaker_label_from_meta():
    src = SourceRef(kind="podcast", speakers=[
        {"name": "Jonathan Ross", "role": "Groq's founder", "primary": True},
        {"name": "David Senra"},
    ])
    assert speaker_label(src, "{name}, {role}") == "Jonathan Ross, Groq's founder"
    # missing role collapses cleanly, no dangling comma
    src2 = SourceRef(kind="podcast", speakers=[{"name": "David Senra"}])
    assert speaker_label(src2, "{name}, {role}") == "David Senra"
    # no structured speakers -> None, never a guess
    assert speaker_label(SourceRef(kind="podcast")) is None


# -- doctor hygiene ---------------------------------------------------------- #

def test_doctor_flags_and_quarantines_foreign_files(workspace, vtt_file):
    entry = _entry(workspace, vtt_file)
    clips = entry.clips_dir
    clips.mkdir(parents=True, exist_ok=True)
    # The exact contamination from the QA session:
    (clips / "burn_he_captions.py").write_text("# ad-hoc")
    (clips / ".venv-burn").mkdir()
    (clips / "preview_01.jpg").write_bytes(b"\xff\xd8")
    (clips / "CAPTION_VERIFY.md").write_text("audit")
    (clips / "21-108.ass").write_text("[Script Info]")
    # ...and legitimate artifacts that must NOT be flagged:
    (clips / "21-108-clip.mp4").write_bytes(b"")
    (clips / "21-108-clip.json").write_text("{}")
    (clips / "21-108-clip.card.html").write_text("<html>")
    (clips / "21-108.en.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")

    report = check(workspace)
    flagged = {f["path"].rsplit("/", 1)[-1] for f in report["library"]["foreign"]}
    assert flagged == {"burn_he_captions.py", ".venv-burn", "preview_01.jpg",
                       "CAPTION_VERIFY.md", "21-108.ass"}

    report = check(workspace, fix=True)
    assert all(f.get("moved_to") for f in report["library"]["foreign"])
    assert not (clips / ".venv-burn").exists()
    assert (workspace.library_dir / "_scratch").is_dir()
    # clean after quarantine
    assert check(workspace)["library"]["foreign"] == []


def test_doctor_reports_ffmpeg_and_settings(workspace):
    report = check(workspace)
    assert set(report["ffmpeg"]) >= {"ffmpeg", "subtitles", "drawtext", "notes"}
    assert report["settings"]["exists"] is False
    assert report["settings"]["effective"]["clip"]["captions"] == "off"


def test_doctor_ffmpeg_note_prints_the_fix(workspace, monkeypatch):
    """A libass-less ffmpeg: doctor must not just report the gap — it points
    to the fix, in step with the clip warning and the README."""
    import openpod.asr as asr
    monkeypatch.setattr(asr, "ffmpeg_capabilities",
                        lambda *a, **k: {"ffmpeg": True, "subtitles": False,
                                         "drawtext": False})
    notes = check(workspace)["ffmpeg"]["notes"]
    blob = " ".join(notes)
    assert "libass" in blob
    assert "ffmpeg-full" in blob                 # the concrete fix
    assert "README install section" in blob


# -- clip presentation layer -------------------------------------------------- #

def test_clip_soft_captions_and_export_dir(workspace, vtt_file, tiny_wav_file, tmp_path):
    entry = _entry(workspace, vtt_file)
    out = tmp_path / "working"
    from openpod.clip import clip

    r = clip(entry.entry_id, 5, 20, workspace=workspace,
             audio_path=str(tiny_wav_file), captions="soft",
             out_dir=str(out))
    # master clean in library, captions sidecar, copies in working folder
    assert r.path.exists() and str(workspace.library_dir) in str(r.path)
    assert r.captions_path is not None and r.captions_path.exists()
    assert r.captions["coverage"]["ok"]
    assert r.export_dir == out
    names = {p.name for p in r.export_paths}
    assert r.path.name in names and r.captions_path.name in names


def test_clip_burn_without_export_dir_degrades_honestly(workspace, vtt_file,
                                                        tiny_wav_file):
    entry = _entry(workspace, vtt_file)
    from openpod.clip import clip

    r = clip(entry.entry_id, 5, 20, workspace=workspace,
             audio_path=str(tiny_wav_file), captions="burn")
    assert r.captions_path is not None            # soft sidecar delivered
    assert "export destination" in r.capability_note
    # and nothing was burned anywhere near the library
    assert not any(p.name.endswith("-social" + r.path.suffix)
                   for p in r.path.parent.iterdir())


def test_clip_label_refuses_to_guess(workspace, vtt_file, tiny_wav_file):
    entry = _entry(workspace, vtt_file)
    from openpod.clip import clip

    r = clip(entry.entry_id, 5, 20, workspace=workspace,
             audio_path=str(tiny_wav_file), label=True)
    assert r.label is None
    assert "refusing to guess" in r.capability_note
