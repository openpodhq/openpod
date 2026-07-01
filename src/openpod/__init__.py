"""OpenPod — local-first briefings for long-form audio & video.

Everything in this package runs on the user's own machine. Nothing is uploaded
to an OpenPod server; the only "corpus" is the user's local library on disk.

Public API surface (kept small on purpose):

    from openpod import Transcript, Cue, SourceRef, Workspace, Library
    from openpod import catch, search, clip
"""

from __future__ import annotations

from .models import Cue, Idea, SearchHit, SourceRef, Transcript
from .config import Workspace
from .library import Library, LibraryEntry

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "Cue",
    "Transcript",
    "SourceRef",
    "Idea",
    "SearchHit",
    "Workspace",
    "Library",
    "LibraryEntry",
]


def __getattr__(name: str):
    # Lazily expose the high-level verbs so importing the package doesn't drag
    # in ingestion / search machinery until it's actually used.
    if name == "catch":
        from .catch import catch

        return catch
    if name == "search":
        from .search import search

        return search
    if name == "clip":
        from .clip import clip

        return clip
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
