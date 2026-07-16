import pytest

from openpod.catch import catch
from openpod.clip import ASPECT_FILTERS, _force_style, _hex_to_ass, clip, first_use_questions


def _entry(workspace, vtt_file):
    return catch("https://example.com/ep1", workspace=workspace,
                 kind="podcast", transcript_path=str(vtt_file)).entry


# -- first-use setup ----------------------------------------------------------- #

def test_first_use_block_until_setup_done(workspace, vtt_file, tiny_wav_file):
    entry = _entry(workspace, vtt_file)
    r = clip(entry.entry_id, 5, 20, workspace=workspace,
             audio_path=str(tiny_wav_file))
    assert r.first_use is not None
    assert r.first_use["ask_once"]
    settings = {q["setting"] for q in r.first_use["questions"]}
    assert {"clip.captions", "clip.aspect",
            "clip.caption_style.color", "clip.export_dir"} <= settings
    # every question is multiple choice — options provided, nothing to write
    assert all(q["options"] for q in r.first_use["questions"])
    # burned captions are the first (recommended-for-social) option
    cap_q = next(q for q in r.first_use["questions"]
                 if q["setting"] == "clip.captions")
    assert cap_q["options"][0]["value"] == "burn"
    assert "never ask twice" in r.first_use["instructions"].lower()

    workspace.set_setting("clip.setup_done", True)
    r2 = clip(entry.entry_id, 5, 20, workspace=workspace,
              audio_path=str(tiny_wav_file))
    assert r2.first_use is None


def test_setup_done_is_a_documented_setting(workspace):
    workspace.set_setting("clip.setup_done", True)
    assert workspace.get_setting("clip.setup_done") is True
    workspace.set_setting("clip.aspect", "vertical")
    assert workspace.effective_settings()["clip"]["aspect"] == "vertical"
    workspace.set_setting("clip.caption_style.color", "#FFE14D")
    assert workspace.effective_settings()["clip"]["caption_style"]["color"] == "#FFE14D"


def test_first_use_questions_platform_hints(workspace):
    q = first_use_questions(workspace)
    aspect_q = next(x for x in q["questions"] if x["setting"] == "clip.aspect")
    labels = " ".join(o["label"] for o in aspect_q["options"])
    assert "TikTok" in labels and "YouTube" in labels


# -- aspect --------------------------------------------------------------------- #

def test_aspect_validation(workspace, vtt_file, tiny_wav_file):
    entry = _entry(workspace, vtt_file)
    with pytest.raises(ValueError):
        clip(entry.entry_id, 5, 20, workspace=workspace,
             audio_path=str(tiny_wav_file), aspect="portrait")


def test_aspect_setting_flows_to_result(workspace, vtt_file, tiny_wav_file):
    workspace.set_setting("clip.aspect", "vertical")
    entry = _entry(workspace, vtt_file)
    r = clip(entry.entry_id, 5, 20, workspace=workspace,
             audio_path=str(tiny_wav_file))
    assert r.aspect == "vertical"
    # audio-only source: framing degrades honestly, master untouched
    assert "video track" in (r.capability_note or "") or r.export_dir is None


def test_aspect_filters_are_centered_crops():
    assert ASPECT_FILTERS["original"] is None
    assert "9/16" in ASPECT_FILTERS["vertical"]
    assert "min(iw,ih)" in ASPECT_FILTERS["square"]
    assert "16/9" in ASPECT_FILTERS["wide"]


# -- caption styling --------------------------------------------------------------- #

def test_hex_to_ass_reverses_bytes():
    assert _hex_to_ass("#FFE14D") == "&H004DE1FF"
    assert _hex_to_ass("#FFFFFF", alpha="60") == "&H60FFFFFF"


def test_force_style_from_settings():
    s = _force_style({"color": "#FFE14D", "outline": "#000000",
                      "boxed": True, "position": "bottom",
                      "font": "Heebo"})
    assert "FontName=Heebo" in s
    assert "PrimaryColour=&H004DE1FF" in s
    assert "BorderStyle=4" in s          # boxed
    assert "Alignment=2" in s            # bottom-center
    s2 = _force_style({"boxed": False, "position": "top"})
    assert "BorderStyle=1" in s2 and "Alignment=8" in s2
    assert "FontName" not in s2          # no font -> renderer default
