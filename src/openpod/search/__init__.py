"""Local, zero-infrastructure search over the user's library.

Keyword retrieval is SQLite FTS5 (standard library). An optional lightweight
local embedding re-rank adds semantic recall without any model download or
server. This is the whole "search many episodes" feature — no corpus, no cloud.
"""

from __future__ import annotations

from typing import Optional

from ..config import Workspace
from ..library import Library
from ..models import SearchHit
from .index import SearchIndex

__all__ = ["SearchIndex", "search", "reindex"]


def search(query: str, *, workspace: Optional[Workspace] = None,
           limit: int = 10, semantic: bool = True) -> list[SearchHit]:
    idx = SearchIndex(workspace or Workspace())
    return idx.search(query, limit=limit, semantic=semantic)


def reindex(workspace: Optional[Workspace] = None) -> int:
    """Rebuild the index from every artifact in the library. Returns cue count."""
    ws = workspace or Workspace()
    idx = SearchIndex(ws)
    idx.rebuild(Library(ws))
    return idx.count()
