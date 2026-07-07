"""Workspace evidence scan — the raw material for a no-effort interview.

The persona vision is **we guess, the user chooses**: instead of asking cold
open questions ("what are you working on?"), the agent presents concrete,
multi-select guesses mined from what's already on disk. This module does the
deterministic half: a fast, bounded, read-only sweep of the workspace that
surfaces *evidence* — project folders, document titles, ``CLAUDE.md``
headings, what the library and follows already reveal — for the agent to turn
into options.

Boundaries and cost, by design:

- **Read-only.** Scanning never writes; it's a read command.
- **Local and explicit.** Only the workspace root is scanned by default;
  extra roots (another projects folder, ``~/.claude``) are scanned only when
  the user points at them. The *agent's* global memory is the agent's asset —
  the skill tells it to mine that through its own access, not through us.
- **Names and headings, not contents.** We read directory names, filenames,
  and the first couple of KB of markdown files for their title/headings.
  Depth, file count, and bytes are all capped, so a scan is milliseconds even
  on a big tree.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from .config import Workspace

# Directories that are build/tool noise, never user intent.
_IGNORE = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
    "dist", "build", ".next", ".turbo", "target", ".idea", ".vscode",
    ".openpod", ".DS_Store",
}
_MAX_DEPTH = 3         # folders below this are implementation detail
_MAX_DOCS = 120        # markdown files inspected, tops
_HEAD_BYTES = 4096     # how much of a file we read for a title/headings


def scan_workspace(workspace: Optional[Workspace] = None,
                   extra_roots: Optional[list[str]] = None) -> dict:
    """Sweep the workspace (plus any explicitly named roots) for persona
    evidence. Returns a JSON-able dict of candidates, each traceable to the
    path it came from."""
    ws = workspace or Workspace()
    roots = [ws.root]
    for r in extra_roots or []:
        p = Path(r).expanduser()
        if p.is_dir():
            roots.append(p)

    projects: list[dict] = []
    docs: list[dict] = []
    claude_files: list[dict] = []
    for root in roots:
        projects.extend(_projects(root))
        d, c = _docs_and_claude(root)
        docs.extend(d)
        claude_files.extend(c)

    return {
        "roots": [str(r) for r in roots],
        "projects": projects,
        "docs": docs[:_MAX_DOCS],
        "claude_files": claude_files,
        "library": _library_signal(ws),
        "follows": _follows_signal(ws),
        "hint": (
            "Turn this evidence into multi-select guesses the user confirms "
            "— projects become 'current projects' options, doc titles become "
            "amplify-topic options (suggest an angle per topic), library "
            "themes and follows corroborate. Always include a free-text "
            "escape hatch; never make the user type what you could have "
            "guessed."
        ),
    }


def _projects(root: Path) -> list[dict]:
    """Top-level directories, most recently touched first — the best cheap
    proxy for 'what is this person actively working on'."""
    out = []
    try:
        entries = [p for p in root.iterdir()
                   if p.is_dir() and p.name not in _IGNORE
                   and not p.name.startswith(".")]
    except OSError:
        return []
    for p in entries:
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        out.append({
            "name": p.name,
            "path": str(p),
            "modified": time.strftime("%Y-%m-%d", time.localtime(mtime)),
            "_mtime": mtime,
        })
    out.sort(key=lambda d: d.pop("_mtime"), reverse=True)
    return out


def _docs_and_claude(root: Path) -> tuple[list[dict], list[dict]]:
    """Markdown titles + CLAUDE.md headings, bounded walk."""
    docs: list[dict] = []
    claude: list[dict] = []
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).parts) - root_depth
        if depth >= _MAX_DEPTH:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames
                       if d not in _IGNORE and not d.startswith(".")]
        for name in filenames:
            if not name.lower().endswith(".md"):
                continue
            path = Path(dirpath) / name
            rel = str(path.relative_to(root))
            if name.upper() == "CLAUDE.MD":
                claude.append({"path": rel,
                               "headings": _headings(path)})
            elif len(docs) < _MAX_DOCS:
                docs.append({"title": _title(path), "path": rel})
    return docs, claude


def _head(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(_HEAD_BYTES).splitlines()
    except OSError:
        return []


def _title(path: Path) -> str:
    for line in _head(path):
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem.replace("_", " ").replace("-", " ")


def _headings(path: Path) -> list[str]:
    return [line.lstrip("#").strip() for line in _head(path)
            if line.startswith("#")][:12]


def _library_signal(ws: Workspace) -> dict:
    """What the caught library already reveals (same counters as derive)."""
    from collections import Counter

    from .briefing import _tokens
    from .library import Library

    shows: Counter = Counter()
    terms: Counter = Counter()
    n = 0
    for entry in Library(ws):
        n += 1
        shows[entry.show()] += 1
        transcript = entry.read_transcript()
        if transcript:
            terms.update(_tokens(transcript.text()))
    return {
        "episodes": n,
        "top_shows": [s for s, _ in shows.most_common(5)],
        "themes": [t for t, _ in terms.most_common(15)],
    }


def _follows_signal(ws: Workspace) -> list[dict]:
    from .follows import Follows

    return [f.to_dict() for f in Follows(ws).list()]
