"""Optional player sync — push artifacts up, pull heard/bookmarks down (Phase 6).

OpenPod stays local-first and pull-only by default. This module is the *opt-in*
bridge to a paired player/sync server for people who run one: it uploads the
derived artifacts (segment recommendations, transcripts, follows, the entry map)
and pulls back what the player learned (bookmarks, heard cues). Nothing here runs
unless the user explicitly logs in and calls ``openpod sync``/``openpod pull``.

Design constraints (house rules):

* **Standard-library HTTP only.** Uses :mod:`urllib.request`; no ``httpx``/
  ``requests`` hard dependency. The ``sync`` extra exists as metadata so the
  feature is discoverable, but installs nothing.
* **Injectable transport.** Every network call goes through a small ``transport``
  callable so tests run fully offline with a fake that records requests and
  returns canned responses — zero sockets.
* **Credentials are secrets.** The bearer token lives in
  ``.openpod/credentials`` (JSON, ``chmod 600``), which is git-ignored. Login
  prints a loud note.
* **Identity parity.** Every ``episode_key`` is computed by
  :mod:`openpod.identity`, the byte-for-byte port of the player's
  ``schemas/src/identity.ts``.
"""

from __future__ import annotations

import gzip
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .config import Workspace
from .deeplink import build_deeplink
from .identity import source_episode_key
from .library import Library
from .models import ListenRecord, SourceRef, format_timestamp

DEFAULT_BASE_URL = "http://localhost:8787"

# The fenced block that bookmark write-back owns inside notes.md (§6.5). Prose
# outside these markers is the user's and is preserved verbatim.
BOOKMARKS_START = "<!-- openpod:player-bookmarks -->"
BOOKMARKS_END = "<!-- /openpod:player-bookmarks -->"


# --------------------------------------------------------------------------- #
# Transport — the single seam tests mock
# --------------------------------------------------------------------------- #


@dataclass
class Response:
    """A minimal HTTP response the sync functions consume."""

    status: int
    body: bytes = b""

    def json(self) -> Any:
        if not self.body:
            return {}
        return json.loads(self.body.decode("utf-8"))


# A transport takes a fully-formed request and returns a :class:`Response`.
Transport = Callable[["Request"], Response]


@dataclass
class Request:
    method: str
    url: str
    headers: dict[str, str]
    body: Optional[bytes] = None


def urllib_transport(req: Request) -> Response:
    """The real transport: one urllib round-trip, no third-party deps."""
    r = urllib.request.Request(
        req.url, data=req.body, headers=req.headers, method=req.method
    )
    try:
        with urllib.request.urlopen(r) as resp:  # noqa: S310 - user-supplied base
            return Response(status=resp.status, body=resp.read())
    except urllib.error.HTTPError as e:  # surface status + body, don't raise
        return Response(status=e.code, body=e.read())


# --------------------------------------------------------------------------- #
# Credentials store
# --------------------------------------------------------------------------- #


@dataclass
class Credentials:
    token: str
    base_url: str
    email: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = {"token": self.token, "base_url": self.base_url}
        if self.email:
            d["email"] = self.email
        return d


def _credentials_path(ws: Workspace) -> Path:
    return ws.dot / "credentials"


def load_credentials(ws: Workspace) -> Optional[Credentials]:
    path = _credentials_path(ws)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return Credentials(
        token=data["token"],
        base_url=data.get("base_url", DEFAULT_BASE_URL),
        email=data.get("email"),
    )


def save_credentials(ws: Workspace, creds: Credentials) -> Path:
    ws.dot.mkdir(parents=True, exist_ok=True)
    path = _credentials_path(ws)
    path.write_text(
        json.dumps(creds.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    try:  # tighten to owner-only; best-effort on platforms without chmod
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - platform dependent
        pass
    _ensure_gitignored(ws)
    return path


def _ensure_gitignored(ws: Workspace) -> None:
    """Make sure ``.openpod/credentials`` can't be committed by accident.

    The whole ``.openpod/`` dir is already ignored at the repo root, but a user
    might keep the library inside a tracked project without that rule, so drop a
    local ``.openpod/.gitignore`` covering the secret regardless.
    """
    local = ws.dot / ".gitignore"
    line = "credentials\n"
    try:
        existing = local.read_text(encoding="utf-8") if local.exists() else ""
        if "credentials" not in existing.split():
            local.write_text(existing + line, encoding="utf-8")
    except OSError:  # pragma: no cover
        pass


def _require_creds(ws: Workspace) -> Credentials:
    creds = load_credentials(ws)
    if creds is None:
        raise RuntimeError(
            "not logged in — run `openpod sync login` first "
            "(needs a paired player; set $OPENPOD_PLAYER_API for a non-default host)"
        )
    return creds


# --------------------------------------------------------------------------- #
# Request helpers
# --------------------------------------------------------------------------- #


def _base_url(base_url: Optional[str] = None) -> str:
    return (base_url or os.environ.get("OPENPOD_PLAYER_API") or DEFAULT_BASE_URL).rstrip(
        "/"
    )


def _call(
    transport: Transport,
    method: str,
    url: str,
    *,
    token: Optional[str] = None,
    json_body: Any = None,
    gzip_body: Optional[bytes] = None,
) -> Response:
    headers: dict[str, str] = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body: Optional[bytes] = None
    if gzip_body is not None:
        body = gzip_body
        headers["Content-Type"] = "application/json"
        headers["Content-Encoding"] = "gzip"
    elif json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    return transport(Request(method=method, url=url, headers=headers, body=body))


def _raise_for_pro(resp: Response) -> None:
    if resp.status == 402:
        raise RuntimeError(
            "this endpoint needs OpenPod Pro — the push/pull ran but the server "
            "declined (402 pro_required). Upgrade to enable cross-device sync."
        )


# --------------------------------------------------------------------------- #
# Login — device flow (§6.3)
# --------------------------------------------------------------------------- #


def login(
    ws: Workspace,
    base_url: Optional[str] = None,
    *,
    transport: Transport = urllib_transport,
    on_prompt: Optional[Callable[[str, str], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
    max_polls: int = 900,
) -> Credentials:
    """Run the OAuth-style device flow and persist the returned token.

    ``on_prompt(user_code, verification_uri)`` is called once so the caller can
    show the user where to authorize; polling then continues at the server's
    ``interval`` until the token is ready, denied, or expired.
    """
    base = _base_url(base_url)
    start = _call(transport, "POST", f"{base}/v1/auth/device", json_body={})
    if start.status >= 400:
        raise RuntimeError(f"device login failed (HTTP {start.status})")
    info = start.json()
    device_code = info["deviceCode"]
    user_code = info["userCode"]
    verification_uri = info["verificationUri"]
    interval = float(info.get("interval", 5))

    if on_prompt is not None:
        on_prompt(user_code, verification_uri)

    for _ in range(max_polls):
        poll = _call(
            transport,
            "POST",
            f"{base}/v1/auth/device/token",
            json_body={"deviceCode": device_code},
        )
        data = poll.json()
        status = data.get("status")
        if status == "ready":
            creds = Credentials(
                token=data["token"], base_url=base, email=data.get("email")
            )
            save_credentials(ws, creds)
            return creds
        if status == "expired":
            raise RuntimeError("device code expired — run `openpod sync login` again")
        if status == "denied":
            raise RuntimeError("device authorization was denied")
        sleep(interval)
    raise RuntimeError("device login timed out")


# --------------------------------------------------------------------------- #
# Segment emission (§6.4)
# --------------------------------------------------------------------------- #

# A loose ULID-ish counter so ids are stable-ish within a run but unique.
def _seg_id(entry_id: str, start: float) -> str:
    stamp = f"{entry_id}:{start:.3f}"
    import hashlib

    return "seg_" + hashlib.sha1(stamp.encode("utf-8")).hexdigest()[:20]


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _episode_block(source: Optional[SourceRef]) -> dict[str, Any]:
    """The §6.4 ``episode`` object drawn from the entry's SourceRef."""
    if source is None:
        return {"feed_url": ""}
    block: dict[str, Any] = {"feed_url": source.url or ""}
    if source.guid:
        block["guid"] = source.guid
    if source.audio_url:
        block["enclosure_url"] = source.audio_url
    if source.show:
        block["show"] = source.show
    if source.title:
        block["title"] = source.title
    if source.published:
        block["published"] = source.published  # derived-rung key parity (§6.5)
    return block


def build_segment_artifacts(entry) -> list[dict[str, Any]]:
    """Build §6.4 ``segment_recommendation`` artifacts for one library entry.

    We combine the detected beats (``segment_transcript``) with the ideas the
    agent already extracted, so each recommendation lands on the best anchor in
    the ladder: a creator chapter when the beat coincides with one, else the
    detected beat, else the exact deeplink moment.
    """
    from .segments import segment_transcript

    transcript = entry.read_transcript()
    if transcript is None:
        return []
    source = entry.source()
    chapters = source.chapters if source else None
    segments = segment_transcript(transcript, chapters=chapters)
    if not segments:
        return []

    episode = _episode_block(source)
    duration = transcript.duration
    artifacts: list[dict[str, Any]] = []
    for seg in segments:
        end = seg.end if seg.end is not None else duration
        anchor = "chapter" if seg.origin == "chapters" else "segment"
        deeplink = build_deeplink(source, seg.start) if source else None
        if anchor == "segment" and deeplink is not None:
            anchor = "deeplink"
        artifact = {
            "kind": "segment_recommendation",
            "schema": 1,
            "id": _seg_id(entry.entry_id, seg.start),
            "episode": episode,
            "openpod_entry_id": entry.entry_id,
            "start": round(float(seg.start), 3),
            "end": round(float(end), 3),
            "anchor": anchor,
            "anchor_start": round(float(seg.start), 3),
            "title": seg.title or "Untitled segment",
            "source": "agent",
            "created_at": _iso_now(),
        }
        if deeplink:
            artifact["reason"] = f"beat starts here — {deeplink}"
        artifacts.append(artifact)
    return artifacts


def build_entry_map(entry) -> Optional[dict[str, Any]]:
    """Build one ``/v1/entry-map`` row for an entry, keyed via identity parity."""
    source = entry.source()
    key, _conf = source_episode_key(source)
    if not key:
        return None
    row: dict[str, Any] = {
        "openpodEntryId": entry.entry_id,
        "feedUrl": source.url,
        "episodeKey": key,
    }
    if source.guid:
        row["guid"] = source.guid
    if source.audio_url:
        row["enclosureUrl"] = source.audio_url
    return row


# --------------------------------------------------------------------------- #
# Push
# --------------------------------------------------------------------------- #


def push_segments(
    ws: Workspace, *, transport: Transport = urllib_transport
) -> dict[str, Any]:
    """Emit §6.4 artifacts across the library and POST them (idempotent on id)."""
    creds = _require_creds(ws)
    artifacts: list[dict[str, Any]] = []
    for entry in Library(ws):
        artifacts.extend(build_segment_artifacts(entry))
    if not artifacts:
        return {"emitted": 0, "accepted": 0, "duplicates": 0}
    resp = _call(
        transport,
        "POST",
        f"{creds.base_url}/v1/segments:batch",
        token=creds.token,
        json_body={"segments": artifacts},
    )
    _raise_for_pro(resp)
    data = resp.json() if resp.status < 400 else {}
    return {
        "emitted": len(artifacts),
        "accepted": data.get("accepted", 0),
        "duplicates": data.get("duplicates", 0),
    }


def push_entry_map(
    ws: Workspace, *, transport: Transport = urllib_transport
) -> dict[str, Any]:
    """POST the openpod-entry-id ↔ episode-key map (identity parity guaranteed)."""
    creds = _require_creds(ws)
    entries = [row for entry in Library(ws) if (row := build_entry_map(entry))]
    if not entries:
        return {"entries": 0}
    resp = _call(
        transport,
        "POST",
        f"{creds.base_url}/v1/entry-map",
        token=creds.token,
        json_body={"entries": entries},
    )
    _raise_for_pro(resp)
    return {"entries": len(entries)}


def push_transcripts(
    ws: Workspace, *, transport: Transport = urllib_transport
) -> dict[str, Any]:
    """PUT each entry's transcript (gzipped JSON) under its episode key."""
    creds = _require_creds(ws)
    pushed = 0
    for entry in Library(ws):
        transcript = entry.read_transcript()
        source = entry.source()
        key, _conf = source_episode_key(source)
        if transcript is None or not key:
            continue
        payload = {
            "episodeKey": key,
            "cues": [
                # A point cue (end is None) gets end == start: the server's
                # TranscriptDocSchema requires a numeric end (a null would 400
                # the whole gzipped PUT, so the OSS transcript would never win
                # rung-1). Zero-width is harmless — cueWindow treats it as a point.
                {"start": round(c.start, 3),
                 "end": round(c.end if c.end is not None else c.start, 3),
                 "text": c.text}
                for c in transcript.cues
            ],
            "source": transcript.source,
        }
        gz = gzip.compress(json.dumps(payload).encode("utf-8"))
        resp = _call(
            transport,
            "PUT",
            f"{creds.base_url}/v1/transcripts/{key}",
            token=creds.token,
            gzip_body=gz,
        )
        _raise_for_pro(resp)
        if resp.status < 400:
            pushed += 1
    return {"transcripts": pushed}


def push_follows(
    ws: Workspace, *, transport: Transport = urllib_transport
) -> dict[str, Any]:
    """PUT the local follow list to the account (feedUrl + provenance)."""
    from .follows import Follows

    creds = _require_creds(ws)
    follows = Follows(ws).list()
    payload = [
        {
            "feedUrl": f.url,
            **({"title": f.title} if f.title else {}),
            "provenance": f.source or "openpod",
        }
        for f in follows
    ]
    resp = _call(
        transport,
        "PUT",
        f"{creds.base_url}/v1/me/follows",
        token=creds.token,
        json_body={"follows": payload},
    )
    if resp.status >= 400 and resp.status != 402:
        raise RuntimeError(f"push follows failed (HTTP {resp.status})")
    return {"follows": len(payload)}


def push_persona(
    ws: Workspace, *, transport: Transport = urllib_transport
) -> dict[str, Any]:
    """PUT the persona blob (opaque to the server). Best-effort; Pro only."""
    from .persona import Persona

    creds = _require_creds(ws)
    persona = Persona(ws)
    blob = persona.read()
    if not blob:
        return {"persona": 0}
    resp = _call(
        transport,
        "PUT",
        f"{creds.base_url}/v1/persona",
        token=creds.token,
        json_body={"blob": blob, "updatedAt": _iso_now()},
    )
    _raise_for_pro(resp)
    return {"persona": 1 if resp.status < 400 else 0}


# --------------------------------------------------------------------------- #
# Bookmark write-back (§6.5) — the fenced-block splice
# --------------------------------------------------------------------------- #


def splice_fenced_block(
    text: str, start_marker: str, end_marker: str, block: str
) -> str:
    """Replace the content *between* two markers, preserving all other prose.

    Mirrors :func:`openpod.persona._splice_section`'s guarantees, but the section
    is delimited by an explicit start/end pair (HTML comments) rather than a
    trailing heading. If the markers are absent the fenced block is appended at
    the end; if present, only the body between them is swapped — everything
    outside the fence (the user's own notes) is byte-preserved.
    """
    body = block.rstrip("\n")
    fence = f"{start_marker}\n{body}\n{end_marker}"

    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip() == start_marker), None)
    if start is not None:
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].strip() == end_marker),
            None,
        )
        if end is not None:
            new_lines = lines[:start] + fence.splitlines() + lines[end + 1 :]
            return "\n".join(new_lines).rstrip("\n") + "\n"
        # Start marker but no end (corrupted): replace ONLY the start-marker line
        # with the full fence, and keep everything after it. Consuming to EOF
        # would silently eat any user prose written below a hand-deleted end
        # marker — never destroy the user's own text.
        new_lines = lines[:start] + fence.splitlines() + lines[start + 1 :]
        return "\n".join(new_lines).rstrip("\n") + "\n"

    prefix = text.rstrip("\n")
    joined = (prefix + "\n\n" if prefix else "") + fence
    return joined.rstrip("\n") + "\n"


def _render_bookmark_line(bm: dict[str, Any], source: Optional[SourceRef]) -> str:
    t = float(bm.get("t", 0.0))
    moment = f"▸ {format_timestamp(t)}"
    deeplink = build_deeplink(source, t) if source else None
    stamp = f"[{moment}]({deeplink})" if deeplink else f"`{moment}`"
    note = (bm.get("note") or "").strip()
    excerpt = ""
    if note:
        one_line = " ".join(note.split())
        excerpt = f" — {one_line[:117]}…" if len(one_line) > 120 else f" — {one_line}"
    end = bm.get("end")
    span = f" (to {format_timestamp(float(end))})" if end is not None else ""
    return f"- {stamp}{span}{excerpt}"


def _bookmarks_ledger_path(entry) -> Path:
    return entry.dir / "bookmarks.json"


def _merge_bookmark_ledger(entry, incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union newly-pulled bookmarks into the per-entry ledger, deduped by id.

    The server's ?unpulled=1 delivers each bookmark exactly once, so the fenced
    block must ACCUMULATE across pulls — rendering only the latest batch would
    silently drop every previously-pulled bookmark from the user's notes. The
    ledger (bookmarks.json, mirroring listened.json) is the durable full set the
    block is rendered from."""
    path = _bookmarks_ledger_path(entry)
    existing: list[dict[str, Any]] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            existing = data.get("bookmarks", []) if isinstance(data, dict) else []
        except (json.JSONDecodeError, OSError):
            existing = []
    by_id: dict[str, dict[str, Any]] = {}
    for bm in [*existing, *incoming]:
        bid = str(bm.get("id") or f"{bm.get('t')}:{bm.get('note')}")
        by_id[bid] = bm  # incoming wins on a repeat id (edited note/range)
    merged = sorted(by_id.values(), key=lambda b: float(b.get("t", 0.0)))
    entry.dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"bookmarks": merged}, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return merged


def pull_bookmarks(
    ws: Workspace, *, transport: Transport = urllib_transport
) -> dict[str, Any]:
    """Pull unpulled bookmarks and write them into each entry's notes.md fence.

    Bookmarks are grouped by ``openpod_entry_id``, merged into a durable per-entry
    ledger (so the fenced block accumulates every bookmark ever pulled, not just
    the latest batch), then the block is spliced idempotently — preserving the
    user's surrounding prose. A bookmark whose entry isn't in the local library is
    surfaced (offer to ``catch`` it) rather than failing the whole pull.
    """
    creds = _require_creds(ws)
    resp = _call(
        transport,
        "GET",
        f"{creds.base_url}/v1/bookmarks?unpulled=1",
        token=creds.token,
    )
    if resp.status >= 400:
        raise RuntimeError(f"pull bookmarks failed (HTTP {resp.status})")
    bookmarks = resp.json().get("bookmarks", [])

    grouped: dict[str, list[dict[str, Any]]] = {}
    orphans: list[dict[str, Any]] = []
    library = Library(ws)
    for bm in bookmarks:
        entry_id = bm.get("openpodEntryId") or bm.get("openpod_entry_id")
        if entry_id and library.get(entry_id) is not None:
            grouped.setdefault(entry_id, []).append(bm)
        else:
            orphans.append(bm)

    written: list[str] = []
    for entry_id, group in grouped.items():
        entry = library.get(entry_id)
        source = entry.source()
        # Merge into the durable ledger, then render the block from the FULL set.
        full = _merge_bookmark_ledger(entry, group)
        lines = ["_Bookmarks pulled from the player. This block is machine-owned "
                 "— edit outside the markers._", ""]
        lines += [_render_bookmark_line(bm, source) for bm in full]
        block = "\n".join(lines)

        existing = (
            entry.notes_path.read_text(encoding="utf-8")
            if entry.notes_path.exists()
            else ""
        )
        entry.dir.mkdir(parents=True, exist_ok=True)
        entry.notes_path.write_text(
            splice_fenced_block(existing, BOOKMARKS_START, BOOKMARKS_END, block),
            encoding="utf-8",
        )
        written.append(str(entry.notes_path))

    return {
        "bookmarks": len(bookmarks),
        "entries_written": len(grouped),
        "paths": written,
        "orphans": orphans,
    }


# --------------------------------------------------------------------------- #
# Heard cues (§6.6) — listened.json + merge
# --------------------------------------------------------------------------- #


def _listened_path(entry) -> Path:
    return entry.dir / "listened.json"


def _entry_for_key(ws: Workspace, key: str) -> Optional[Any]:
    """Resolve a library entry by its episode_key (identity parity)."""
    for entry in Library(ws):
        entry_key, _conf = source_episode_key(entry.source())
        if entry_key and entry_key == key:
            return entry
    return None


def _merge_records(
    existing: list[ListenRecord], incoming: list[ListenRecord]
) -> list[ListenRecord]:
    """Merge heard cues by (episode_key, cue_start), keeping the higher count and
    the earliest first-heard timestamp."""
    by_key: dict[tuple[str, float], ListenRecord] = {
        (r.episode_key, round(r.cue_start, 3)): r for r in existing
    }
    for rec in incoming:
        k = (rec.episode_key, round(rec.cue_start, 3))
        prev = by_key.get(k)
        if prev is None:
            by_key[k] = rec
            continue
        prev.heard_count = max(prev.heard_count, rec.heard_count)
        if rec.first_heard_at and (
            not prev.first_heard_at or rec.first_heard_at < prev.first_heard_at
        ):
            prev.first_heard_at = rec.first_heard_at
        if not prev.text and rec.text:
            prev.text = rec.text
        if prev.cue_end is None and rec.cue_end is not None:
            prev.cue_end = rec.cue_end
    return sorted(by_key.values(), key=lambda r: r.cue_start)


def _write_listened(entry, records: list[ListenRecord]) -> Path:
    """Write/merge listened.json for one entry. Returns the path."""
    path = _listened_path(entry)
    existing: list[ListenRecord] = []
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        existing = [ListenRecord.from_dict(r) for r in data.get("heard", [])]
    merged = _merge_records(existing, records)
    entry.dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "entry_id": entry.entry_id,
        "updated_at": _iso_now(),
        "heard": [r.to_dict() for r in merged],
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


def _apply_heard(
    ws: Workspace, records: list[ListenRecord]
) -> dict[str, Any]:
    """Group heard records by episode_key, write listened.json per entry, and
    reindex those entries so the offline search covers heard content.

    Heard cues that don't resolve to a local entry are reported as ``orphans``
    (the episode was heard on the player but never caught locally)."""
    grouped: dict[str, list[ListenRecord]] = {}
    orphans: list[ListenRecord] = []
    for rec in records:
        entry = _entry_for_key(ws, rec.episode_key)
        if entry is None:
            orphans.append(rec)
            continue
        grouped.setdefault(rec.episode_key, []).append(rec)

    written: list[str] = []
    reindexed = 0
    for key, group in grouped.items():
        entry = _entry_for_key(ws, key)
        written.append(str(_write_listened(entry, group)))
        reindexed += _index_heard(ws, entry, group)

    return {
        "heard": len(records),
        "entries_written": len(grouped),
        "paths": written,
        "orphans": [r.to_dict() for r in orphans],
        "indexed": reindexed,
    }


def _index_heard(ws: Workspace, entry, records: list[ListenRecord]) -> int:
    """Fold heard cues into the local search index so they're findable offline.

    The transcript is the primary index source; heard cues carry the same text,
    so re-indexing the entry (which already contains those cues) is enough to
    keep search coverage. We reindex the entry best-effort — if the embeddings
    extra isn't installed or the index is unavailable, we skip silently, since
    listened.json is the source of truth and search is an enhancement.
    """
    try:
        from .search import SearchIndex

        idx = SearchIndex(ws)
        try:
            if not idx.is_indexed(entry.entry_id):
                return idx.add_entry(entry)
            return 0
        finally:
            idx.close()
    except Exception:  # pragma: no cover - search is optional/best-effort
        return 0


def pull_heard(
    ws: Workspace, *, transport: Transport = urllib_transport
) -> dict[str, Any]:
    """GET heard cues from the server and write listened.json per entry (§6.6)."""
    creds = _require_creds(ws)
    resp = _call(
        transport,
        "GET",
        f"{creds.base_url}/v1/heard?unpulled=1",
        token=creds.token,
    )
    # Hosted heard-sync is a Pro Phase-7 endpoint; older/free servers don't have
    # it. Degrade gracefully instead of a raw HTTP error — the offline path
    # (`openpod import-heard <player-export.json>`) always works.
    if resp.status in (404, 501):
        return {
            "heard": 0,
            "entries_written": 0,
            "paths": [],
            "orphans": [],
            "indexed": 0,
            "unavailable": "Hosted heard sync isn't available on this server yet — "
            "use `openpod import-heard <player-export.json>` instead.",
        }
    if resp.status == 402:
        raise RuntimeError("heard search sync needs OpenPod Pro")
    if resp.status >= 400:
        raise RuntimeError(f"pull heard failed (HTTP {resp.status})")
    raw = resp.json().get("heardCues", resp.json().get("heard", []))
    records = [ListenRecord.from_player(r) for r in raw]
    return _apply_heard(ws, records)


# --------------------------------------------------------------------------- #
# Import a player export file (offline path for heard + follows)
# --------------------------------------------------------------------------- #


def import_heard(ws: Workspace, path: str | Path) -> dict[str, Any]:
    """Import a player export JSON: heard cues → listened.json, follows → local.

    The export shape is ``{kind:"openpod_export", schema, follows:[...],
    heardCues:[...], ...}``. Heard cues become per-entry listened.json (merged);
    follows are added to the local follows.yaml. Fully offline — no network.
    """
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("kind") != "openpod_export":
        raise ValueError(
            f"{p} is not an openpod_export file (kind={data.get('kind')!r})"
        )

    # Heard cues → listened.json + search merge.
    records = [ListenRecord.from_player(c) for c in data.get("heardCues", [])]
    heard_result = _apply_heard(ws, records)

    # Follows → local follows.yaml (best-effort, additive).
    from .follows import Follows

    follows = Follows(ws)
    added = 0
    for f in data.get("follows", []):
        url = f.get("feedUrl") or f.get("url")
        if not url:
            continue
        before = len(follows.list())
        follows.add(url, title=f.get("title"), source=f.get("provenance"))
        if len(follows.list()) > before:
            added += 1

    return {
        "source": str(p),
        "follows_added": added,
        **heard_result,
    }
