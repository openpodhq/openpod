"""Opt-in imports — seed follows + persona from explicit signal made elsewhere.

The user has already declared their interests in other apps: the shows they
subscribe to, the things they save. ``openpod import`` lets them hand that
signal to OpenPod as a **file** (OPML first — universal, zero accounts, zero
egress). The invariants shape the design:

- **Snapshot, not sync.** Each import is a point-in-time, user-initiated run.
  Nothing is polled in the background; re-run it when you want a refresh.
- **Raw exports stage in ``.openpod/imports/``** verbatim, so every import is
  inspectable, re-runnable, and deletable.
- **Subscriptions merge into ``follows.yaml``** de-duplicated by feed URL, each
  tagged with ``source:`` provenance (e.g. ``opml:overcast``) so the user can
  see — and trim — exactly what an import added.
- **Interest signal refreshes the ``## Imported interests (opt-in)`` persona
  block** under the same marker contract as the Derived block: machine-owned,
  regenerable, and never touching the human sections above it.

OpenPod itself never holds OAuth tokens or calls cloud APIs. Cloud-only sources
(Spotify shows, YouTube subs, Reddit saves, X bookmarks) are pulled by the
*agent's* connectors, which hand OpenPod a plain file to import here.
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import Workspace
from .follows import Follow, Follows
from .models import slugify
from .persona import IMPORTED_MARKER, Persona


@dataclass
class ImportResult:
    """What one import run did — every path it touched, for the UI to report."""

    source_label: str                      # provenance tag, e.g. "opml:overcast"
    staged_path: Path                      # verbatim copy under imports/
    follows_path: Path
    persona_path: Path
    added: list[Follow] = field(default_factory=list)
    skipped: int = 0                       # already-followed feeds (deduped)

    def to_dict(self) -> dict:
        return {
            "source_label": self.source_label,
            "staged_path": str(self.staged_path),
            "follows_path": str(self.follows_path),
            "persona_path": str(self.persona_path),
            "added": [f.to_dict() for f in self.added],
            "skipped": self.skipped,
        }


def import_opml(path: str, *, workspace: Optional[Workspace] = None,
                label: Optional[str] = None) -> ImportResult:
    """Import podcast subscriptions from an OPML export.

    Stages the file, merges its feeds into ``follows.yaml`` (skipping feeds
    already followed), and refreshes the imported-interests persona block from
    everything staged so far.
    """
    ws = workspace or Workspace()
    src = Path(path).expanduser()
    if not src.is_file():
        raise ValueError(
            f"no such file: {src} — export OPML from your podcast app "
            "(Overcast/Pocket Casts/Apple Podcasts) and pass its path"
        )

    feeds = parse_opml(src.read_text(encoding="utf-8"))
    if not feeds:
        raise ValueError(
            f"no feeds found in {src.name} — is it an OPML subscription export?"
        )

    source_label = label or f"opml:{slugify(src.stem)}"
    staged = _stage(ws, src)

    follows = Follows(ws)
    before = {f.url for f in follows.list()}
    added: list[Follow] = []
    skipped = 0
    for title, url in feeds:
        if url in before:
            skipped += 1
            continue
        follow = follows.add(url, title=title, source=source_label)
        if follow.url in before:
            skipped += 1
        else:
            before.add(follow.url)
            added.append(follow)

    persona = Persona(ws)
    refresh_imported_interests(ws)

    return ImportResult(source_label=source_label, staged_path=staged,
                        follows_path=ws.follows_file,
                        persona_path=persona.path, added=added,
                        skipped=skipped)


def refresh_imported_interests(workspace: Optional[Workspace] = None) -> str:
    """Regenerate the ``## Imported interests (opt-in)`` persona block from the
    provenance-tagged follows. Idempotent; the human sections are untouched."""
    ws = workspace or Workspace()
    imported = [f for f in Follows(ws).list() if f.source]
    by_source: dict[str, list[Follow]] = {}
    for f in imported:
        by_source.setdefault(f.source, []).append(f)

    if not by_source:
        block = "_No imports yet — `openpod import <file.opml>` to seed this._"
    else:
        lines = [
            "_Auto-refreshed by `openpod import` from the explicit signal you",
            "made elsewhere (subscriptions, saves). Regenerable; your own",
            "sections above are never edited. Promote anything here into",
            '"Interests (amplify)" in your own words._',
            "",
        ]
        for source, fs in sorted(by_source.items()):
            titles = ", ".join(sorted(f.title or f.url for f in fs))
            lines.append(f"- **{source}** ({len(fs)} subscriptions): {titles}")
        block = "\n".join(lines)

    Persona(ws).replace_section(IMPORTED_MARKER, block)
    return block


# --------------------------------------------------------------------------- #
# OPML parsing
# --------------------------------------------------------------------------- #


def parse_opml(text: str) -> list[tuple[Optional[str], str]]:
    """Extract ``(title, feed_url)`` pairs from OPML, tolerating the attribute
    spellings and nesting the exporters in the wild actually produce."""
    root = ET.fromstring(text)
    feeds: list[tuple[Optional[str], str]] = []
    seen: set[str] = set()
    for outline in root.iter("outline"):
        url = _attr(outline, "xmlUrl")
        if not url or url in seen:
            continue
        seen.add(url)
        title = _attr(outline, "title") or _attr(outline, "text")
        feeds.append((title, url))
    return feeds


def _attr(el: ET.Element, name: str) -> Optional[str]:
    for key, value in el.attrib.items():
        if key.lower() == name.lower() and value.strip():
            return value.strip()
    return None


def _stage(ws: Workspace, src: Path) -> Path:
    """Copy the raw export verbatim into ``imports/`` (a re-run of the same
    file refreshes the snapshot in place)."""
    ws.imports_dir.mkdir(parents=True, exist_ok=True)
    dest = ws.imports_dir / src.name
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    return dest
