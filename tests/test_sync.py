"""Offline tests for the player-sync bridge.

Every network call goes through an injected fake transport that records the
request and returns a canned response — no sockets, no server. The library
fixtures are written directly with write_meta + write_transcript + write_ideas.
"""

from __future__ import annotations

import gzip
import json
import os

import pytest

from openpod import sync
from openpod.identity import source_episode_key
from openpod.library import Library
from openpod.models import Cue, SourceRef, Transcript
from openpod.sync import (
    BOOKMARKS_END,
    BOOKMARKS_START,
    Credentials,
    Request,
    Response,
    save_credentials,
    splice_fenced_block,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


class FakeTransport:
    """Records requests; returns responses from a routing table.

    ``routes`` maps ``"METHOD /path"`` (path only, query stripped) to either a
    :class:`Response` or a zero-arg callable returning one.
    """

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.requests: list[Request] = []

    def __call__(self, req: Request) -> Response:
        self.requests.append(req)
        from urllib.parse import urlsplit

        path = urlsplit(req.url).path
        key = f"{req.method} {path}"
        handler = self.routes.get(key)
        if handler is None:
            return Response(status=404, body=b"{}")
        return handler() if callable(handler) else handler

    def sent(self, method: str, path_fragment: str) -> list[Request]:
        return [
            r
            for r in self.requests
            if r.method == method and path_fragment in r.url
        ]


def _json_response(payload, status=200) -> Response:
    return Response(status=status, body=json.dumps(payload).encode("utf-8"))


@pytest.fixture
def logged_in(workspace):
    """A workspace with stored credentials pointing at a fake base URL."""
    save_credentials(
        workspace,
        Credentials(token="tok-123", base_url="https://player.test", email="me@x.com"),
    )
    return workspace


def _make_entry(workspace, *, show="Test Pod", title="Episode One: Consensus"):
    """A tiny library entry: meta.json (SourceRef) + transcript.json + ideas.md."""
    library = Library(workspace)
    source = SourceRef(
        kind="podcast",
        url="https://example.com/feed",
        guid="ep-001",
        audio_url="https://cdn.example.com/ep1.mp3",
        show=show,
        title=title,
        published="2026-07-01",
    )
    entry = library.entry_for(source)
    entry.write_meta(source)
    transcript = Transcript(
        cues=[
            Cue(start=0.0, end=5.0, text="Welcome to the show about consensus."),
            Cue(start=5.0, end=11.0, text="Raft is easier to understand than Paxos."),
            Cue(start=60.0, end=66.0, text="A note on tail latency in practice."),
        ],
        source="podcast:transcript",
    )
    entry.write_transcript(transcript)
    entry.write_ideas("- Raft vs Paxos\n- Tail latency\n")
    return entry


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #


def test_login_stores_credentials_via_device_flow(workspace):
    poll_states = iter([{"status": "pending"}, {"status": "ready",
                                                "token": "abc",
                                                "deviceId": "dev1",
                                                "email": "user@example.com"}])
    transport = FakeTransport({
        "POST /v1/auth/device": _json_response({
            "deviceCode": "dc", "userCode": "WXYZ-1234",
            "verificationUri": "https://player.test/activate",
            "interval": 0, "expiresIn": 600,
        }),
        "POST /v1/auth/device/token": lambda: _json_response(next(poll_states)),
    })
    prompts = []

    creds = sync.login(
        workspace,
        base_url="https://player.test",
        transport=transport,
        on_prompt=lambda code, uri: prompts.append((code, uri)),
        sleep=lambda _s: None,
    )

    assert creds.token == "abc"
    assert creds.email == "user@example.com"
    assert prompts == [("WXYZ-1234", "https://player.test/activate")]

    # Credentials persisted, chmod 600 (POSIX; Windows has no such bits),
    # git-ignored.
    stored = sync.load_credentials(workspace)
    assert stored is not None and stored.token == "abc"
    cred_path = sync._credentials_path(workspace)
    if os.name == "posix":
        assert (cred_path.stat().st_mode & 0o777) == 0o600
    ignore = (workspace.dot / ".gitignore").read_text(encoding="utf-8")
    assert "credentials" in ignore


def test_login_raises_on_denied(workspace):
    transport = FakeTransport({
        "POST /v1/auth/device": _json_response({
            "deviceCode": "dc", "userCode": "AAAA",
            "verificationUri": "u", "interval": 0,
        }),
        "POST /v1/auth/device/token": _json_response({"status": "denied"}),
    })
    with pytest.raises(RuntimeError, match="denied"):
        sync.login(workspace, transport=transport, sleep=lambda _s: None)


# --------------------------------------------------------------------------- #
# Segment emission (§6.4) + entry map
# --------------------------------------------------------------------------- #


def test_build_segment_artifacts_shape(logged_in):
    entry = _make_entry(logged_in)
    artifacts = sync.build_segment_artifacts(entry)
    assert artifacts
    art = artifacts[0]
    # Exact §6.4 shape.
    assert art["kind"] == "segment_recommendation"
    assert art["schema"] == 1
    assert art["id"].startswith("seg_")
    assert art["openpod_entry_id"] == entry.entry_id
    assert art["episode"]["feed_url"] == "https://example.com/feed"
    assert art["episode"]["guid"] == "ep-001"
    assert art["episode"]["enclosure_url"] == "https://cdn.example.com/ep1.mp3"
    assert art["source"] == "agent"
    assert art["anchor"] in ("chapter", "segment", "deeplink")
    assert isinstance(art["start"], float)
    assert isinstance(art["end"], float)
    assert art["anchor_start"] == art["start"]
    assert "created_at" in art


def test_build_entry_map_uses_identity_parity(logged_in):
    entry = _make_entry(logged_in)
    row = sync.build_entry_map(entry)
    key, conf = source_episode_key(entry.source())
    assert conf == "guid"  # our fixture has a guid
    assert row["episodeKey"] == key
    assert row["openpodEntryId"] == entry.entry_id
    assert row["feedUrl"] == "https://example.com/feed"
    assert row["guid"] == "ep-001"


def test_push_segments_posts_artifacts(logged_in):
    _make_entry(logged_in)
    transport = FakeTransport({
        "POST /v1/segments:batch": _json_response({"accepted": 2, "duplicates": 0}),
    })
    result = sync.push_segments(logged_in, transport=transport)
    assert result["emitted"] >= 1
    assert result["accepted"] == 2
    sent = transport.sent("POST", "/v1/segments:batch")
    assert len(sent) == 1
    body = json.loads(sent[0].body)
    assert body["segments"]
    assert all(s["kind"] == "segment_recommendation" for s in body["segments"])
    assert sent[0].headers["Authorization"] == "Bearer tok-123"


def test_push_segments_raises_on_402(logged_in):
    _make_entry(logged_in)
    transport = FakeTransport({
        "POST /v1/segments:batch": _json_response({"error": "pro_required"}, status=402),
    })
    with pytest.raises(RuntimeError, match="Pro"):
        sync.push_segments(logged_in, transport=transport)


def test_push_entry_map_posts_keyed_rows(logged_in):
    _make_entry(logged_in)
    transport = FakeTransport({"POST /v1/entry-map": _json_response({})})
    result = sync.push_entry_map(logged_in, transport=transport)
    assert result["entries"] == 1
    body = json.loads(transport.sent("POST", "/v1/entry-map")[0].body)
    assert body["entries"][0]["episodeKey"]


def test_push_transcripts_gzips_body(logged_in):
    entry = _make_entry(logged_in)
    key, _ = source_episode_key(entry.source())
    transport = FakeTransport({f"PUT /v1/transcripts/{key}": _json_response({})})
    result = sync.push_transcripts(logged_in, transport=transport)
    assert result["transcripts"] == 1
    sent = transport.sent("PUT", "/v1/transcripts/")[0]
    assert sent.headers["Content-Encoding"] == "gzip"
    decoded = json.loads(gzip.decompress(sent.body).decode("utf-8"))
    assert decoded["episodeKey"] == key
    assert decoded["cues"][0]["text"].startswith("Welcome")


def test_push_transcripts_point_cue_end_is_start_not_null(logged_in):
    # A point cue (end=None) must serialize end == start: the server's
    # TranscriptDocSchema requires a numeric end, so a null would 400 the whole
    # gzipped PUT and the OSS transcript would never win rung-1.
    library = Library(logged_in)
    source = SourceRef(kind="podcast", url="https://example.com/feed",
                       guid="pt-1", show="S", title="T")
    entry = library.entry_for(source)
    entry.write_meta(source)
    entry.write_transcript(Transcript(
        cues=[Cue(start=1.0, end=4.0, text="has end"),
              Cue(start=9.0, end=None, text="point cue no end")],
        source="podcast:transcript",
    ))
    key, _ = source_episode_key(source)
    transport = FakeTransport({f"PUT /v1/transcripts/{key}": _json_response({})})
    sync.push_transcripts(logged_in, transport=transport)
    decoded = json.loads(gzip.decompress(
        transport.sent("PUT", "/v1/transcripts/")[0].body).decode("utf-8"))
    ends = [c["end"] for c in decoded["cues"]]
    assert None not in ends  # never null on the wire
    assert decoded["cues"][1]["end"] == decoded["cues"][1]["start"]  # point cue


def test_pull_heard_degrades_when_endpoint_missing(logged_in):
    # Hosted heard sync is a Phase-7 endpoint; a server without it returns 404.
    # pull_heard must degrade (offer import-heard), not hard-error.
    transport = FakeTransport({"GET /v1/heard": _json_response({}, status=404)})
    result = sync.pull_heard(logged_in, transport=transport)
    assert result["heard"] == 0
    assert "import-heard" in result["unavailable"]


def test_splice_missing_end_marker_keeps_user_prose(logged_in):
    # A hand-deleted end marker must not cause the splice to eat the user's prose
    # written below the (now unterminated) block.
    corrupted = f"# Notes\n\n{BOOKMARKS_START}\n- old\n\nMy essay below the block.\n"
    out = splice_fenced_block(corrupted, BOOKMARKS_START, BOOKMARKS_END, "fresh")
    assert "My essay below the block." in out  # not eaten
    assert out.count(BOOKMARKS_END) == 1  # end marker restored
    assert "fresh" in out


def test_push_follows(logged_in):
    from openpod.follows import Follows

    Follows(logged_in).add("https://example.com/feed", title="Test Pod")
    transport = FakeTransport({"PUT /v1/me/follows": _json_response({})})
    result = sync.push_follows(logged_in, transport=transport)
    assert result["follows"] == 1
    body = json.loads(transport.sent("PUT", "/v1/me/follows")[0].body)
    assert body["follows"][0]["feedUrl"] == "https://example.com/feed"
    assert body["follows"][0]["provenance"] == "openpod"


# --------------------------------------------------------------------------- #
# Bookmark write-back (§6.5) — fenced-block splice
# --------------------------------------------------------------------------- #


def test_splice_inserts_when_absent():
    out = splice_fenced_block("", BOOKMARKS_START, BOOKMARKS_END, "hello")
    assert BOOKMARKS_START in out
    assert BOOKMARKS_END in out
    assert "hello" in out


def test_splice_preserves_user_prose_around_block():
    original = "# My notes\n\nSomething I wrote.\n"
    out = splice_fenced_block(original, BOOKMARKS_START, BOOKMARKS_END, "block v1")
    assert "# My notes" in out
    assert "Something I wrote." in out
    assert "block v1" in out


def test_splice_is_idempotent_and_never_duplicates():
    original = "# My notes\n\nUser prose above.\n"
    once = splice_fenced_block(original, BOOKMARKS_START, BOOKMARKS_END, "v1")
    twice = splice_fenced_block(once, BOOKMARKS_START, BOOKMARKS_END, "v2")
    # Exactly one fence, prose preserved, body replaced not appended.
    assert twice.count(BOOKMARKS_START) == 1
    assert twice.count(BOOKMARKS_END) == 1
    assert "User prose above." in twice
    assert "v2" in twice
    assert "v1" not in twice
    # A user adding prose *below* the block keeps it across the next splice.
    with_trailer = twice + "\nMore user prose after.\n"
    third = splice_fenced_block(with_trailer, BOOKMARKS_START, BOOKMARKS_END, "v3")
    assert "User prose above." in third
    assert "More user prose after." in third
    assert third.count(BOOKMARKS_START) == 1


def test_pull_bookmarks_writes_fenced_block(logged_in):
    entry = _make_entry(logged_in)
    entry.notes_path.write_text("# Notes\n\nMy own thoughts.\n", encoding="utf-8")
    transport = FakeTransport({
        "GET /v1/bookmarks": _json_response({"bookmarks": [
            {"id": "b1", "episodeKey": "k", "t": 65.0, "note": "great point on latency",
             "createdAt": "2026-07-01T00:00:00Z", "openpodEntryId": entry.entry_id},
            {"id": "b2", "episodeKey": "k", "t": 5.0, "note": "raft vs paxos",
             "createdAt": "2026-07-01T00:00:00Z", "openpodEntryId": entry.entry_id},
        ]}),
    })
    result = sync.pull_bookmarks(logged_in, transport=transport)
    assert result["entries_written"] == 1
    notes = entry.notes_path.read_text(encoding="utf-8")
    assert "My own thoughts." in notes  # user prose preserved
    assert BOOKMARKS_START in notes and BOOKMARKS_END in notes
    assert "great point on latency" in notes
    # Sorted by time: 0:05 line before 1:05 line.
    assert notes.index("raft vs paxos") < notes.index("great point on latency")
    # Deep-link rendered as an OpenPod player link (feed + guid) at the
    # bookmark time — never the raw enclosure, which would open in the OS
    # default podcast app.
    assert "https://player.openpod.dev/e/" in notes
    assert "guid=ep-001&t=65" in notes
    assert "cdn.example.com/ep1.mp3" not in notes


def test_pull_bookmarks_second_pull_is_idempotent(logged_in):
    entry = _make_entry(logged_in)
    entry.notes_path.write_text("Prose.\n", encoding="utf-8")
    routes = {
        "GET /v1/bookmarks": _json_response({"bookmarks": [
            {"id": "b1", "t": 5.0, "note": "n1", "openpodEntryId": entry.entry_id},
        ]}),
    }
    sync.pull_bookmarks(logged_in, transport=FakeTransport(routes))
    first = entry.notes_path.read_text(encoding="utf-8")
    sync.pull_bookmarks(logged_in, transport=FakeTransport(routes))
    second = entry.notes_path.read_text(encoding="utf-8")
    assert first == second
    assert second.count(BOOKMARKS_START) == 1
    assert "Prose." in second


def test_pull_bookmarks_accumulate_across_pulls(logged_in):
    # ?unpulled=1 delivers each bookmark once: pull 1 gets b1, pull 2 gets b2.
    # The fenced block must ACCUMULATE — b1 must survive pull 2, not be replaced.
    entry = _make_entry(logged_in)
    entry.notes_path.write_text("# Notes\n\nUser prose.\n", encoding="utf-8")
    sync.pull_bookmarks(logged_in, transport=FakeTransport({
        "GET /v1/bookmarks": _json_response({"bookmarks": [
            {"id": "b1", "t": 5.0, "note": "first idea", "openpodEntryId": entry.entry_id},
        ]}),
    }))
    sync.pull_bookmarks(logged_in, transport=FakeTransport({
        "GET /v1/bookmarks": _json_response({"bookmarks": [
            {"id": "b2", "t": 65.0, "note": "second idea", "openpodEntryId": entry.entry_id},
        ]}),
    }))
    notes = entry.notes_path.read_text(encoding="utf-8")
    assert "first idea" in notes  # not lost when b2 arrives
    assert "second idea" in notes
    assert notes.count(BOOKMARKS_START) == 1
    assert "User prose." in notes
    # sorted by time inside the block
    assert notes.index("first idea") < notes.index("second idea")
    # the durable ledger holds both
    ledger = json.loads((entry.dir / "bookmarks.json").read_text(encoding="utf-8"))
    assert {b["id"] for b in ledger["bookmarks"]} == {"b1", "b2"}


def test_pull_bookmarks_orphan_offered_not_failed(logged_in):
    _make_entry(logged_in)
    transport = FakeTransport({
        "GET /v1/bookmarks": _json_response({"bookmarks": [
            {"id": "b9", "t": 1.0, "note": "x", "openpodEntryId": "ghost/episode"},
        ]}),
    })
    result = sync.pull_bookmarks(logged_in, transport=transport)
    assert result["entries_written"] == 0
    assert len(result["orphans"]) == 1


# --------------------------------------------------------------------------- #
# Heard cues (§6.6) — pull + import round-trip
# --------------------------------------------------------------------------- #


def test_pull_heard_writes_listened_json(logged_in):
    entry = _make_entry(logged_in)
    key, _ = source_episode_key(entry.source())
    transport = FakeTransport({
        "GET /v1/heard": _json_response({"heardCues": [
            {"episodeKey": key, "cueStart": 0.0, "cueEnd": 5.0,
             "text": "Welcome", "firstHeardAt": "2026-07-01T00:00:00Z",
             "heardCount": 2},
            {"episodeKey": key, "cueStart": 60.0, "cueEnd": 66.0,
             "text": "tail latency", "firstHeardAt": "2026-07-02T00:00:00Z",
             "heardCount": 1},
        ]}),
    })
    result = sync.pull_heard(logged_in, transport=transport)
    assert result["entries_written"] == 1
    listened = json.loads((entry.dir / "listened.json").read_text(encoding="utf-8"))
    assert listened["entry_id"] == entry.entry_id
    assert len(listened["heard"]) == 2
    assert listened["heard"][0]["heard_count"] == 2


def test_pull_heard_merges_new_text_into_transcript_for_offline_search(logged_in):
    # Heard content that ISN'T in the local transcript (e.g. server ASR) must
    # become findable offline — pull --heard merges it into transcript.json
    # (task 6.6) without overwriting existing cues.
    entry = _make_entry(logged_in)  # transcript has cues at 0/5/60
    key, _ = source_episode_key(entry.source())
    transport = FakeTransport({
        "GET /v1/heard": _json_response({"cues": [
            {"episodeKey": key, "cueStart": 900.0, "cueEnd": 906.0,
             "text": "a distinctive phrase never in the local transcript",
             "firstHeardAt": 1000, "heardCount": 1},
        ]}),
    })
    sync.pull_heard(logged_in, transport=transport)
    merged = entry.read_transcript()
    texts = [c.text for c in merged.cues]
    assert "a distinctive phrase never in the local transcript" in texts  # merged in
    assert "Welcome to the show about consensus." in texts  # original preserved
    starts = [c.start for c in merged.cues]
    assert starts == sorted(starts)  # kept in time order


def test_import_heard_round_trips_export_file(workspace, tmp_path):
    entry = _make_entry(workspace)
    key, _ = source_episode_key(entry.source())
    export = {
        "kind": "openpod_export",
        "schema": 1,
        "exportedAt": "2026-07-07T00:00:00Z",
        "email": "me@x.com",
        "follows": [
            {"feedUrl": "https://example.com/feed", "title": "Test Pod",
             "provenance": "player"},
            {"feedUrl": "https://other.example.com/feed", "provenance": "player"},
        ],
        "events": [],
        "userState": [],
        "heardCues": [
            {"episodeKey": key, "cueStart": 5.0, "cueEnd": 11.0,
             "text": "Raft is easier", "firstHeardAt": "2026-07-01T00:00:00Z",
             "heardCount": 3},
        ],
    }
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps(export), encoding="utf-8")

    result = sync.import_heard(workspace, export_path)
    assert result["heard"] == 1
    assert result["entries_written"] == 1
    assert result["follows_added"] >= 1  # at least the non-followed one

    listened = json.loads((entry.dir / "listened.json").read_text(encoding="utf-8"))
    assert listened["heard"][0]["text"] == "Raft is easier"
    assert listened["heard"][0]["heard_count"] == 3

    from openpod.follows import Follows

    urls = {f.url for f in Follows(workspace).list()}
    assert "https://other.example.com/feed" in urls


def test_import_heard_merges_on_repeat(workspace, tmp_path):
    entry = _make_entry(workspace)
    key, _ = source_episode_key(entry.source())

    def _export(count):
        return {
            "kind": "openpod_export", "schema": 1, "follows": [],
            "heardCues": [{"episodeKey": key, "cueStart": 5.0, "cueEnd": 11.0,
                           "text": "Raft", "firstHeardAt": "2026-07-01T00:00:00Z",
                           "heardCount": count}],
        }

    p1 = tmp_path / "e1.json"
    p1.write_text(json.dumps(_export(1)), encoding="utf-8")
    sync.import_heard(workspace, p1)

    p2 = tmp_path / "e2.json"
    p2.write_text(json.dumps(_export(4)), encoding="utf-8")
    sync.import_heard(workspace, p2)

    listened = json.loads((entry.dir / "listened.json").read_text(encoding="utf-8"))
    # One merged record, higher count wins, no duplication.
    assert len(listened["heard"]) == 1
    assert listened["heard"][0]["heard_count"] == 4


def test_import_heard_rejects_wrong_kind(workspace, tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"kind": "something_else"}), encoding="utf-8")
    with pytest.raises(ValueError, match="openpod_export"):
        sync.import_heard(workspace, p)


def test_requires_login(workspace):
    _make_entry(workspace)
    with pytest.raises(RuntimeError, match="not logged in"):
        sync.push_segments(workspace, transport=FakeTransport())
