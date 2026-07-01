"""SQLite FTS5 keyword index + optional local embedding re-rank."""

from __future__ import annotations

import re
import sqlite3
from typing import Iterable, Optional

from ..config import Workspace
from ..deeplink import build_deeplink
from ..models import SearchHit, Transcript
from . import embed

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


class SearchIndex:
    """Owns the ``.openpod/index/search.db`` FTS index."""

    def __init__(self, workspace: Optional[Workspace] = None) -> None:
        self.workspace = workspace or Workspace()
        self.workspace.index_dir.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.workspace.index_db))
        self._embedder = embed.get_embedder()
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self._db.cursor()
        cur.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS cues USING fts5("
            " text, entry_id UNINDEXED, show UNINDEXED, episode UNINDEXED,"
            " start UNINDEXED, tokenize='porter unicode61')"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS vectors ("
            " rowid INTEGER PRIMARY KEY, vec BLOB)"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS indexed ("
            " entry_id TEXT PRIMARY KEY, cue_count INTEGER)"
        )
        self._db.commit()

    # -- indexing ----------------------------------------------------------- #

    def add_entry(self, entry) -> int:
        """(Re)index one library entry. Returns number of cues indexed."""
        transcript = entry.read_transcript()
        if transcript is None:
            return 0
        source = entry.source()
        show = entry.show()
        episode = entry.title()

        self.remove_entry(entry.entry_id)
        cur = self._db.cursor()
        count = 0
        for cue in transcript.cues:
            if not cue.text.strip():
                continue
            cur.execute(
                "INSERT INTO cues(text, entry_id, show, episode, start)"
                " VALUES (?,?,?,?,?)",
                (cue.text, entry.entry_id, show, episode, cue.start),
            )
            rowid = cur.lastrowid
            vec = self._embedder.embed(cue.text)
            cur.execute(
                "INSERT INTO vectors(rowid, vec) VALUES (?,?)",
                (rowid, embed.pack(vec)),
            )
            count += 1
        cur.execute(
            "INSERT OR REPLACE INTO indexed(entry_id, cue_count) VALUES (?,?)",
            (entry.entry_id, count),
        )
        self._db.commit()
        return count

    def remove_entry(self, entry_id: str) -> None:
        cur = self._db.cursor()
        rows = cur.execute(
            "SELECT rowid FROM cues WHERE entry_id = ?", (entry_id,)
        ).fetchall()
        ids = [r[0] for r in rows]
        if ids:
            qmarks = ",".join("?" * len(ids))
            cur.execute(f"DELETE FROM vectors WHERE rowid IN ({qmarks})", ids)
        cur.execute("DELETE FROM cues WHERE entry_id = ?", (entry_id,))
        cur.execute("DELETE FROM indexed WHERE entry_id = ?", (entry_id,))
        self._db.commit()

    def rebuild(self, library: Iterable) -> None:
        cur = self._db.cursor()
        cur.execute("DELETE FROM cues")
        cur.execute("DELETE FROM vectors")
        cur.execute("DELETE FROM indexed")
        self._db.commit()
        for entry in library:
            self.add_entry(entry)

    def count(self) -> int:
        return self._db.execute("SELECT count(*) FROM cues").fetchone()[0]

    def is_indexed(self, entry_id: str) -> bool:
        return self._db.execute(
            "SELECT 1 FROM indexed WHERE entry_id = ?", (entry_id,)
        ).fetchone() is not None

    # -- querying ----------------------------------------------------------- #

    def search(self, query: str, *, limit: int = 10,
               semantic: bool = True, pool: int = 60) -> list[SearchHit]:
        query = query.strip()
        if not query:
            return []

        rows = self._fts(query, pool)
        if not rows and semantic:
            rows = self._all_rows(pool * 4)  # fall back to a pure semantic scan
        if not rows:
            return []

        if semantic:
            qvec = self._embedder.embed(query)
            scored = []
            for row in rows:
                sim = embed.cosine(qvec, embed.unpack(row["vec"])) if row["vec"] else 0.0
                blended = 0.6 * row["kw"] + 0.4 * ((sim + 1) / 2)
                scored.append((blended, row))
            scored.sort(key=lambda x: x[0], reverse=True)
            ranked = [(s, r) for s, r in scored[:limit]]
        else:
            ranked = [(r["kw"], r) for r in rows[:limit]]

        return [self._to_hit(score, r) for score, r in ranked]

    def _fts(self, query: str, pool: int) -> list[dict]:
        match = _to_match(query)
        if not match:
            return []
        try:
            cur = self._db.execute(
                "SELECT c.rowid, c.entry_id, c.show, c.episode, c.start, c.text,"
                " bm25(cues) AS rank, v.vec"
                " FROM cues c LEFT JOIN vectors v ON v.rowid = c.rowid"
                " WHERE cues MATCH ? ORDER BY rank LIMIT ?",
                (match, pool),
            )
            raw = cur.fetchall()
        except sqlite3.OperationalError:
            return []
        # bm25: lower is better; map to (0,1] similarity.
        out = []
        for rowid, entry_id, show, episode, start, text, rank, vec in raw:
            out.append({
                "rowid": rowid, "entry_id": entry_id, "show": show,
                "episode": episode, "start": start, "text": text,
                "kw": 1.0 / (1.0 + max(rank, 0.0)), "vec": vec,
            })
        return out

    def _all_rows(self, cap: int) -> list[dict]:
        cur = self._db.execute(
            "SELECT c.rowid, c.entry_id, c.show, c.episode, c.start, c.text, v.vec"
            " FROM cues c LEFT JOIN vectors v ON v.rowid = c.rowid LIMIT ?",
            (cap,),
        )
        return [{
            "rowid": r[0], "entry_id": r[1], "show": r[2], "episode": r[3],
            "start": r[4], "text": r[5], "kw": 0.0, "vec": r[6],
        } for r in cur.fetchall()]

    def _to_hit(self, score: float, row: dict) -> SearchHit:
        deeplink = None
        entry = _load_entry(self.workspace, row["entry_id"])
        if entry is not None:
            src = entry.source()
            if src is not None:
                deeplink = build_deeplink(src, row["start"])
        return SearchHit(
            show=row["show"], episode=row["episode"], text=row["text"],
            start=row["start"], score=round(score, 4), deeplink=deeplink,
            entry_id=row["entry_id"],
        )

    def close(self) -> None:
        self._db.close()


def _to_match(query: str) -> str:
    """Turn free text into a safe FTS5 MATCH string (OR of quoted terms)."""
    if '"' in query:  # let power users pass raw phrase queries
        return query
    terms = _TOKEN.findall(query)
    if not terms:
        return ""
    return " OR ".join(f'"{t}"' for t in terms)


def _load_entry(workspace: Workspace, entry_id: str):
    from ..library import Library

    return Library(workspace).get(entry_id)
