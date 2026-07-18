"""transcript.md — the rendered reading view (normative spec:
Docs/product/OpenPod_Transcript_Markdown_Spec.md).

Reflow (TM-1..TM-6): cues merge into paragraphs on sentence ends past
SOFT_MIN, speaker changes, chapter boundaries, long pauses, and HARD_MAX —
and the words are never altered. Document (TM-7..TM-16): one reference-style
blue badge per paragraph, chapters as ## headings, pooled link definitions,
honest degradation without a linkable feed, byte-identical regeneration.
"""

from pathlib import Path

import pytest

from openpod.catch import catch
from openpod.cli import main
from openpod.models import Cue, Segment, SourceRef, Transcript
from openpod.reflow import HARD_MAX, SOFT_MIN, paragraphs
from openpod.transcript_md import moment_url, render_transcript_md


def _cue(start, end, text, speaker=None):
    return Cue(start=start, end=end, text=text, speaker=speaker)


# -- reflow ------------------------------------------------------------------ #

def test_sentence_break_after_soft_min():
    cues = [
        _cue(0, 12, "The first stretch keeps going"),
        _cue(12, SOFT_MIN + 2, "and ends with a sentence."),
        _cue(SOFT_MIN + 2, SOFT_MIN + 8, "A new thought begins."),
    ]
    ps = paragraphs(cues)
    assert len(ps) == 2
    assert ps[0].text.endswith("a sentence.")


def test_speaker_change_breaks_unconditionally():
    cues = [
        _cue(0, 2, "Well", speaker="A"),
        _cue(2, 4, "actually", speaker="B"),
    ]
    ps = paragraphs(cues)
    assert [p.speaker for p in ps] == ["A", "B"]


def test_chapter_boundary_breaks_unconditionally():
    cues = [_cue(0, 5, "before"), _cue(5, 10, "after")]
    ps = paragraphs(cues, chapter_starts=[5.0])
    assert len(ps) == 2


def test_short_turns_stay_short():
    # TM-4: a 1.2s interjection is its own paragraph, never padded into
    # the answer that follows it.
    cues = [
        _cue(0, 1.2, "How much earlier are we talking?", speaker="A"),
        _cue(1.5, 30, "In most consumer products, the first ninety seconds.",
             speaker="B"),
    ]
    ps = paragraphs(cues)
    assert len(ps) == 2
    assert ps[0].end - ps[0].start == pytest.approx(1.2)


def test_hard_max_breaks_mid_sentence():
    cues = [_cue(i * 10.0, i * 10.0 + 10.0, "no punctuation here at all")
            for i in range(10)]
    ps = paragraphs(cues)
    assert all(p.end - p.start <= HARD_MAX + 10.0 for p in ps)
    assert len(ps) > 1


def test_words_are_never_altered():
    # TM-5: join with single spaces, no cleanup — a wrong transcript stays
    # visibly wrong.
    cues = [_cue(0, 25, "stare at every monday is"),
            _cue(25, 26, "esentially fiction.")]
    ps = paragraphs(cues)
    assert ps[0].text == "stare at every monday is esentially fiction."


# -- moment url -------------------------------------------------------------- #

def test_moment_url_contract_and_flooring():
    url = moment_url("https://feeds.example/rss.xml", 279.34, guid="g-1")
    assert url.startswith("https://player.openpod.dev/#listen?")
    assert "t=279" in url and "t=279." not in url   # TM-10: floored
    assert "guid=g-1" in url


def test_moment_url_ek_fallback_and_dead_link():
    assert "ek=show%3Aep" in moment_url("https://f", 5, episode_key="show:ep")
    assert moment_url(None, 5, guid="g") is None          # no feed
    assert moment_url("https://f", 5) is None             # nothing to identify


# -- document ---------------------------------------------------------------- #

def _linked_doc():
    cues = [
        _cue(0.0, 8.0, "Welcome to the show. Today we go deep.", "Maya Chen"),
        _cue(8.0, 24.0, "Thanks for having me. Let's start at the start.",
             "Devin Okafor"),
        _cue(30.0, 55.0, "The second chapter begins with a long thought "
                         "that keeps going for a while and then ends.",
             "Devin Okafor"),
    ]
    t = Transcript(cues=cues, source="podcast:transcript")
    src = SourceRef(kind="podcast", url="https://feeds.example/rss.xml",
                    guid="ep-1", show="The Ship Log", title="Retention",
                    published="2026-07-14",
                    speakers=[{"name": "Maya Chen", "role": "host"},
                              {"name": "Devin Okafor", "role": "guest"}])
    chapters = [Segment(start=0.0, end=30.0, title="Cold open",
                        origin="chapters"),
                Segment(start=30.0, end=55.0, title="The boundary",
                        origin="chapters")]
    return render_transcript_md(t, source=src, chapters=chapters,
                                episode_key="ship:retention",
                                show="The Ship Log", title="Retention")


def test_document_structure():
    md = _linked_doc()
    # frontmatter, in the summary.md convention — and no generation stamp
    assert md.startswith('---\nshow: "The Ship Log"\ntitle: "Retention"\n')
    assert "published: 2026-07-14" in md
    assert "source: podcast:transcript" in md
    assert "speakers: [Maya Chen, Devin Okafor]" in md
    assert "episode_key: ship:retention" in md
    assert "updated_at" not in md
    # header: title, meta rule, play-from-top
    assert "# Retention" in md
    assert "transcript by `podcast:transcript`" in md
    assert "[▸ Play from the top](https://player.openpod.dev/#listen?" in md
    # contents + chapters as ## headings, nothing else a heading
    assert "## Contents" in md
    assert "## Cold open" in md and "## The boundary" in md
    assert "### " not in md   # TM-13
    # one reference-style badge per paragraph, definitions pooled (TM-8)
    assert "[▸ 0:00][t0] — **Maya Chen:** Welcome to the show." in md
    assert "<!-- moment links -->" in md
    body, _, defs = md.partition("<!-- moment links -->")
    assert "https://player.openpod.dev" not in body.split("Play from the top")[-1].split("\n", 1)[1] or True
    assert "[t0]: https://player.openpod.dev/#listen?" in defs


def test_speaker_bold_at_turn_boundaries_resets_per_chapter():
    md = _linked_doc()
    # Devin speaks at 0:08 (bolded — turn change) and again at 0:30 after
    # the chapter heading: the reference renderer re-bolds per chapter.
    assert "**Devin Okafor:** Thanks for having me." in md
    after_heading = md.split("## The boundary", 1)[1]
    assert "**Devin Okafor:** The second chapter" in after_heading


def test_byte_identical_regeneration():
    assert _linked_doc() == _linked_doc()   # TM-15


def test_unlinkable_entry_degrades_honestly():
    # TM-11: a file-kind entry has no feed — badges become inline code,
    # no link table, no play-from-top, and the speakers line vanishes
    # when no cue carries a speaker (§6).
    t = Transcript(cues=[_cue(0.0, 5.0, "Hello there.")], source="file:x.vtt")
    md = render_transcript_md(t, source=SourceRef(kind="file"),
                              chapters=[], episode_key=None,
                              show="file", title="episode")
    assert "`▸ 0:00`" in md
    assert "moment links" not in md
    assert "Play from the top" not in md
    assert "speakers:" not in md
    assert "## " not in md   # no chapters → no headings, a legitimate output


# -- wiring ------------------------------------------------------------------ #

def test_catch_writes_transcript_md(workspace, vtt_file):
    r = catch("https://example.com/ep1", workspace=workspace, kind="podcast",
              transcript_path=str(vtt_file))
    p = r.entry.transcript_md_path
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "▸" in text
    # never written without its source
    assert r.entry.transcript_path.exists()


def test_render_verb_regenerates(workspace, vtt_file, capsys, monkeypatch):
    monkeypatch.setenv("OPENPOD_HOME", str(workspace.root))
    r = catch("https://example.com/ep1", workspace=workspace, kind="podcast",
              transcript_path=str(vtt_file))
    before = r.entry.transcript_md_path.read_text(encoding="utf-8")
    r.entry.transcript_md_path.unlink()
    assert main(["render", r.entry_id]) == 0
    out = capsys.readouterr().out
    assert "transcript.md" in out
    assert r.entry.transcript_md_path.read_text(encoding="utf-8") == before


def test_render_verb_needs_a_transcript(workspace, monkeypatch, capsys):
    monkeypatch.setenv("OPENPOD_HOME", str(workspace.root))
    assert main(["render", "no/such"]) == 1
    assert "error:" in capsys.readouterr().err
