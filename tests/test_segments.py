"""Segment-aware deep links: creator chapters when the source ships them,
local topic detection otherwise — anchors land at the start of the beat, not
mid-sentence where the keyword fired."""

import json

from openpod.briefing import build_toc
from openpod.catch import catch
from openpod.library import Library
from openpod.models import Cue, SourceRef, Transcript
from openpod.search import search
from openpod.segments import (annotate, parse_description_chapters,
                              segment_at, segment_transcript)

DESCRIPTION = """\
Thomas Laffont joins to break down the AI IPO wave.

0:00 Cold open
2:15 The state of venture
11:05 - The 10x paradox
25:40 Starlink's profit pool
Follow us at example.com
"""


def _yt_source(**kw):
    return SourceRef(kind="youtube", url="https://youtube.com/watch?v=abc",
                     video_id="abc", **kw)


def _transcript(pairs, cue_len=10.0):
    """pairs: list of (start, text)."""
    return Transcript(cues=[Cue(start=s, text=t, end=s + cue_len)
                            for s, t in pairs], source="test")


def _two_topic_transcript():
    """~10 minutes: sourdough for 300s, then databases — one clear boundary."""
    baking = ("sourdough starter flour hydration proofing oven spring crumb "
              "levain fermentation gluten shaping banneton scoring bake")
    infra = ("database index sqlite postgres transaction latency replica "
             "shard schema query planner vacuum checkpoint durability wal")
    pairs = [(i * 10.0, baking) for i in range(30)]
    pairs += [(300.0 + i * 10.0, infra) for i in range(30)]
    return _transcript(pairs)


# --------------------------------------------------------------------------- #
# Creator chapters
# --------------------------------------------------------------------------- #


def test_description_chapters_parse_and_bound():
    chapters = parse_description_chapters(DESCRIPTION, duration=2400.0)
    assert [c["title"] for c in chapters] == [
        "Cold open", "The state of venture", "The 10x paradox",
        "Starlink's profit pool"]
    paradox = chapters[2]
    assert paradox["start"] == 665.0 and paradox["end"] == 1540.0
    assert chapters[-1]["end"] == 2400.0


def test_description_chapters_reject_non_chapters():
    # two timestamps in prose are not structure (YouTube needs 3+, from 0:00)
    assert parse_description_chapters("see 1:23 and also 4:56 for more") == []
    assert parse_description_chapters(
        "5:00 starts late\n6:00 b\n7:00 c") == []


def test_chapters_win_over_topic_detection():
    t = _two_topic_transcript()
    chapters = [  # yt-dlp shape; both under the hybrid split threshold
        {"start_time": 0.0, "end_time": 300.0, "title": "Baking"},
        {"start_time": 300.0, "end_time": 600.0, "title": "Databases"},
    ]
    segs = segment_transcript(t, chapters=chapters)
    assert [s.title for s in segs] == ["Baking", "Databases"]
    assert all(s.origin == "chapters" for s in segs)


# --------------------------------------------------------------------------- #
# Hybrid: over-long chapters get topic sub-segments
# --------------------------------------------------------------------------- #


def test_long_chapter_splits_into_topic_subsegments():
    # one 10-minute creator chapter spanning two clearly distinct topics
    t = _two_topic_transcript()
    chapters = [{"start": 0.0, "end": 600.0, "title": "Bestie Q&A"}]
    segs = segment_transcript(t, chapters=chapters)

    assert len(segs) >= 2, "a 10-minute chapter should be sub-segmented"
    assert all(s.origin == "topic" for s in segs)
    # sub-segments tile the chapter's own bounds exactly
    assert segs[0].start == 0.0 and segs[-1].end == 600.0
    for prev, nxt in zip(segs, segs[1:]):
        assert prev.end == nxt.start
    # a boundary lands near the actual topic change at t=300
    assert any(abs(s.start - 300.0) <= 40.0 for s in segs[1:])
    # the parent chapter title survives as a prefix on every sub-segment
    assert all(s.title and s.title.startswith("Bestie Q&A — ") for s in segs)


def test_hybrid_keeps_short_chapters_and_splits_long_ones():
    t = _two_topic_transcript()
    chapters = [
        {"start": 0.0, "title": "Cold open"},       # 60s: verbatim
        {"start": 60.0, "title": "The long bit"},   # 540s, two topics: split
    ]
    segs = segment_transcript(t, chapters=chapters)

    assert segs[0].title == "Cold open" and segs[0].origin == "chapters"
    assert segs[0].start == 0.0 and segs[0].end == 60.0
    subs = segs[1:]
    assert len(subs) >= 2
    assert all(s.origin == "topic" for s in subs)
    assert subs[0].start == 60.0 and subs[-1].end == 600.0
    assert all(s.title.startswith("The long bit — ") for s in subs)


def test_long_chapter_without_internal_shift_stays_verbatim():
    # 400s of one homogeneous topic: over the threshold, but the detector
    # finds no internal boundary, so the creator's chapter stands
    baking = ("sourdough starter flour hydration proofing oven spring crumb "
              "levain fermentation gluten shaping banneton scoring bake")
    t = _transcript([(i * 10.0, baking) for i in range(40)])
    chapters = [{"start": 0.0, "end": 400.0, "title": "Baking"}]
    segs = segment_transcript(t, chapters=chapters)
    assert [s.title for s in segs] == ["Baking"]
    assert segs[0].origin == "chapters"


# --------------------------------------------------------------------------- #
# Local topic segmentation
# --------------------------------------------------------------------------- #


def test_topic_segmentation_finds_the_shift():
    segs = segment_transcript(_two_topic_transcript())
    assert len(segs) >= 2
    assert all(s.origin == "topic" for s in segs)
    # one boundary lands near the actual topic change at t=300
    assert any(abs(s.start - 300.0) <= 40.0 for s in segs[1:])
    assert all(s.title for s in segs)  # every beat gets a scannable label


def test_topic_segmentation_is_deterministic():
    t = _two_topic_transcript()
    a = [s.to_dict() for s in segment_transcript(t)]
    b = [s.to_dict() for s in segment_transcript(t)]
    assert a == b


def test_short_transcript_yields_single_covering_segment(vtt_file):
    from openpod.transcript import load_file

    t = load_file(str(vtt_file))
    segs = segment_transcript(t)
    assert len(segs) == 1
    assert segs[0].start == t.cues[0].start and segs[0].end == t.duration


# --------------------------------------------------------------------------- #
# Anchoring: ideas, TOC, search hits land on segment starts
# --------------------------------------------------------------------------- #


def test_annotate_anchors_to_containing_segment_start():
    t = _two_topic_transcript()
    chapters = [{"start": 0.0, "title": "Baking"},
                {"start": 300.0, "title": "Databases"}]
    segs = segment_transcript(t, chapters=chapters)
    hit = segment_at(segs, 471.7)
    assert hit.start == 300.0 and hit.title == "Databases"

    from openpod.models import Idea

    idea = Idea(text="x", start=471.7)
    annotate([idea], segs, _yt_source())
    assert idea.segment_start == 300.0
    assert idea.segment_title == "Databases"
    assert idea.segment_deeplink.endswith("&t=300s")  # start of the beat
    assert idea.segment_deeplink != "&t=471s"


def test_toc_uses_real_structure_when_available():
    t = _two_topic_transcript()
    segs = segment_transcript(t, chapters=[
        {"start": 0.0, "title": "Baking"}, {"start": 300.0, "title": "Databases"}])
    toc = build_toc(t, _yt_source(), structure=segs)
    assert [i.text for i in toc] == ["Baking", "Databases"]
    assert toc[1].deeplink.endswith("&t=300s")


def test_catch_persists_segments_and_search_hits_carry_them(workspace,
                                                            vtt_file):
    r = catch("https://example.com/ep1", workspace=workspace, kind="podcast",
              transcript_path=str(vtt_file))
    meta = json.loads(Library(workspace).get(r.entry_id)
                      .meta_path.read_text(encoding="utf-8"))
    assert meta["segments"], "segments persisted at catch time"

    hits = search("raft consensus", workspace=workspace, semantic=False)
    assert hits
    hit = hits[0]
    assert hit.segment_start is not None
    assert hit.segment_start <= hit.start  # the beat starts at/before the cue


# --------------------------------------------------------------------------- #
# The anchor ladder: chapter + beat + moment, labeled, deduped
# --------------------------------------------------------------------------- #


def test_annotate_carries_both_chapter_and_beat_anchors():
    from openpod.models import Idea
    from openpod.segments import chapters_as_segments

    t = _two_topic_transcript()
    # one long creator chapter -> beats layer sub-segments it, chapter layer
    # keeps it verbatim
    raw = [{"start": 0.0, "end": 600.0, "title": "Bestie Q&A"}]
    beats = segment_transcript(t, chapters=raw)
    chapters = chapters_as_segments(raw, t.duration)
    assert len(beats) >= 2 and len(chapters) == 1

    idea = Idea(text="x", start=471.7)
    annotate([idea], beats, _yt_source(), chapters=chapters)
    # beat: articulation start, inside the chapter, before the moment
    assert 0.0 < idea.segment_start <= 471.7
    # chapter: the creator's own anchor, distinct from the beat
    assert idea.chapter_start == 0.0
    assert idea.chapter_title == "Bestie Q&A"
    assert idea.chapter_deeplink.endswith("&t=0s")


def test_annotate_dedupes_chapter_equal_to_beat():
    from openpod.models import Idea
    from openpod.segments import chapters_as_segments

    t = _two_topic_transcript()
    raw = [{"start": 0.0, "end": 300.0, "title": "Baking"},
           {"start": 300.0, "end": 600.0, "title": "Databases"}]
    beats = segment_transcript(t, chapters=raw)      # short chapters: kept
    chapters = chapters_as_segments(raw, t.duration)

    idea = Idea(text="x", start=471.7)
    annotate([idea], beats, _yt_source(), chapters=chapters)
    assert idea.segment_start == 300.0
    assert idea.chapter_start is None  # same anchor — not repeated


def test_ideas_markdown_renders_labeled_ladder():
    from openpod.briefing import ideas_markdown
    from openpod.models import Idea

    idea = Idea(text="the 10x paradox", start=721.0,
                deeplink="https://x/?t=721s",
                segment_start=665.0, segment_deeplink="https://x/?t=665s",
                chapter_start=638.0, chapter_title="The 10x Paradox",
                chapter_deeplink="https://x/?t=638s")
    md = ideas_markdown([idea], _yt_source())
    assert "**[▸ 11:05](https://x/?t=665s)**" in md     # beat is primary
    assert "chapter “The 10x Paradox” [▸ 10:38](https://x/?t=638s)" in md
    assert "said [▸ 12:01](https://x/?t=721s)" in md
    assert "_Links:" in md                             # the one-line legend
    assert "— cited with [OpenPod](https://github.com/openpodhq/openpod?ref=briefing)" in md


def test_search_hits_carry_chapter_layer(workspace, vtt_file):
    r = catch("https://example.com/ep1", workspace=workspace, kind="podcast",
              transcript_path=str(vtt_file))
    # retrofit creator chapters onto the caught entry's source metadata
    entry = Library(workspace).get(r.entry_id)
    meta = json.loads(entry.meta_path.read_text(encoding="utf-8"))
    meta["source"]["chapters"] = [
        {"start": 0.0, "title": "Intro"},
        {"start": 11.0, "title": "Consensus deep dive"},
        {"start": 60.0, "title": "Latency"},
    ]
    entry.meta_path.write_text(json.dumps(meta), encoding="utf-8")

    hits = search("raft consensus", workspace=workspace, semantic=False)
    hit = next(h for h in hits if h.start >= 11.0)
    assert hit.chapter_title == "Consensus deep dive"
    assert hit.chapter_start == 11.0
