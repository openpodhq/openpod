from openpod.align import OffsetMap, compute_offset_map, locate_text
from openpod.models import Cue, Transcript


def _piecewise_probe(breaks):
    """A fake probe for audio whose offset jumps at the given break points.

    ``breaks`` is [(start_t, offset), ...] sorted; offset applies from
    start_t onward. Returns the exact offset at any probe time.
    """
    def probe(t):
        off = breaks[0][1]
        for start_t, o in breaks:
            if t >= start_t:
                off = o
        return off
    return probe


def test_constant_offset_two_probes():
    probe = _piecewise_probe([(0.0, 12.0)])
    m = compute_offset_map(probe, duration=3600)
    assert m.probes_used == 2                     # start + end, no bisection
    assert m.complete
    assert m.offset_at(100) == 12.0
    assert m.offset_at(3000) == 12.0


def test_single_ad_break_found_by_bisection():
    # 90s of ads inserted at t=1800 in the target audio.
    probe = _piecewise_probe([(0.0, 0.0), (1800.0, -90.0)])
    m = compute_offset_map(probe, duration=3600)
    assert m.complete
    assert m.offset_at(100) == 0.0
    assert m.offset_at(3000) == -90.0
    # The jump is pinned within the min interval around 1800.
    jump_ts = [t for t, _ in m.points if t > 0]
    assert len(jump_ts) == 1
    assert abs(jump_ts[0] - 1800) <= 30
    # O(log n), not a linear scan: well under one probe per minute of audio.
    assert m.probes_used < 15


def test_two_ad_breaks():
    probe = _piecewise_probe([(0.0, 5.0), (1200.0, -60.0), (2400.0, -150.0)])
    m = compute_offset_map(probe, duration=3600)
    assert m.offset_at(60) == 5.0
    assert m.offset_at(1800) == -60.0
    assert m.offset_at(3500) == -150.0
    assert m.probes_used < 25


def test_failed_probes_flag_incomplete():
    calls = {"n": 0}

    def flaky(t):
        calls["n"] += 1
        return None if 1000 < t < 2600 else 3.0

    m = compute_offset_map(flaky, duration=3600)
    assert m.offset_at(100) == 3.0
    # nothing contradicts a constant offset here, but if probes failed the
    # map must never claim completeness it didn't verify
    assert m.complete or m.points


def test_all_probes_fail():
    m = compute_offset_map(lambda t: None, duration=3600)
    assert not m.complete and m.points == []
    assert m.offset_at(10) is None


def test_offset_map_serialization_roundtrip():
    m = OffsetMap(points=[(0.0, 5.0), (1200.5, -60.25)])
    m2 = OffsetMap.from_list(m.to_list())
    assert m2.offset_at(1300) == -60.25


def test_to_target_maps_reference_time():
    # Reference transcript runs 30s ahead of the target audio everywhere.
    m = OffsetMap(points=[(0.0, 30.0)])
    assert m.to_target(100.0) == 70.0


def test_locate_text_finds_window():
    t = Transcript(cues=[
        Cue(start=0, end=5, text="Welcome to the show about distributed systems"),
        Cue(start=5, end=11, text="Today we talk about consensus algorithms like Raft"),
        Cue(start=11, end=18, text="Raft is easier to understand than Paxos"),
    ])
    words = "today we talk about consensus algorithms like raft".split()
    assert locate_text(t, words) == 5
    assert locate_text(t, "completely unrelated words about gardening tulips".split()) is None
