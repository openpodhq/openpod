"""Local-file catches must be first-class: distinct files stay distinct
entries, re-catches stay idempotent, and the recorded path lets ``clip`` cut
from media the user already pointed at — without re-passing ``audio_path``.

Regression: every file-kind catch used to slug to ``file/episode`` (no title,
no path recorded), so the second local file silently replaced the first, and
``clip`` refused to cut from an episode whose media sat exactly where the
user said it was.
"""

import pytest

from openpod.catch import catch
from openpod.clip import clip
from openpod.library import Library, _source_conflicts
from openpod.models import SourceRef


def _catch_file(workspace, media, vtt):
    return catch(str(media), workspace=workspace, kind="file",
                 transcript_path=str(vtt))


def test_file_catch_records_title_and_path(workspace, tiny_wav_file, vtt_file):
    r = _catch_file(workspace, tiny_wav_file, vtt_file)
    src = r.entry.source()
    resolved = str(tiny_wav_file.resolve())
    assert src.title == tiny_wav_file.stem
    assert src.url == resolved
    assert src.audio_url == resolved          # media → clip can cut from it
    assert r.entry_id == f"file/{tiny_wav_file.stem}"


def test_transcript_file_as_link_records_path_but_no_media(workspace, vtt_file):
    r = catch(str(vtt_file), workspace=workspace, kind="file")
    src = r.entry.source()
    assert src.title == vtt_file.stem
    assert src.url == str(vtt_file.resolve())
    assert src.audio_url is None              # a .vtt is not cuttable media


def test_two_local_files_are_two_entries(workspace, tmp_path, vtt_file):
    a = tmp_path / "alpha.vtt"
    b = tmp_path / "beta.vtt"
    a.write_text(vtt_file.read_text(), encoding="utf-8")
    b.write_text(vtt_file.read_text(), encoding="utf-8")
    ra = catch(str(a), workspace=workspace, kind="file")
    rb = catch(str(b), workspace=workspace, kind="file")
    assert ra.entry_id != rb.entry_id
    lib = Library(workspace)
    assert lib.get(ra.entry_id) is not None
    assert lib.get(rb.entry_id) is not None


def test_same_name_in_different_folders_does_not_merge(workspace, tmp_path,
                                                       vtt_file):
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir(), d2.mkdir()
    for d in (d1, d2):
        (d / "interview.vtt").write_text(vtt_file.read_text(),
                                         encoding="utf-8")
    r1 = catch(str(d1 / "interview.vtt"), workspace=workspace, kind="file")
    r2 = catch(str(d2 / "interview.vtt"), workspace=workspace, kind="file")
    assert r1.entry_id == "file/interview"
    assert r2.entry_id == "file/interview-2"


def test_recatch_same_file_is_idempotent(workspace, tmp_path, vtt_file):
    p = tmp_path / "again.vtt"
    p.write_text(vtt_file.read_text(), encoding="utf-8")
    r1 = catch(str(p), workspace=workspace, kind="file")
    r2 = catch(str(p), workspace=workspace, kind="file")
    assert r1.entry_id == r2.entry_id
    assert len(Library(workspace)) == 1


def test_clip_cuts_from_recorded_file_path(workspace, tiny_wav_file, vtt_file):
    r = _catch_file(workspace, tiny_wav_file, vtt_file)
    result = clip(r.entry_id, 0.0, 2.0, workspace=workspace)
    assert result.path.exists()


def test_get_media_names_a_moved_file(workspace, tmp_path):
    from openpod.media import get_media

    gone = tmp_path / "gone.mp3"
    src = SourceRef(kind="file", url=str(gone), audio_url=str(gone))
    with pytest.raises(ValueError, match="moved since catch"):
        get_media("file/gone", src, workspace=workspace)


def test_source_conflicts_needs_proof_not_absence():
    stored = {"kind": "podcast", "url": "https://feeds.example/f", "guid": "g1"}
    same_learned_more = SourceRef(kind="podcast", url="https://feeds.example/f",
                                  guid="g1", audio_url="https://cdn/a.mp3")
    other_episode = SourceRef(kind="podcast", url="https://feeds.example/f",
                              guid="g2")
    knows_nothing = SourceRef(kind="podcast")
    assert not _source_conflicts(stored, same_learned_more)
    assert _source_conflicts(stored, other_episode)
    assert not _source_conflicts(stored, knows_nothing)
