"""``export_timestamps`` — emit timed segments + deep-links as JSON or Markdown.

A thin, deterministic export over a caught episode's transcript so an agent (or
a human) can hand the navigable structure to something else.
"""

from __future__ import annotations

import json
from typing import Optional

from . import briefing as _briefing
from . import theme
from .config import Workspace
from .deeplink import build_deeplink
from .library import Library
from .models import format_timestamp


def export_timestamps(entry_id: str, *, workspace: Optional[Workspace] = None,
                      fmt: str = "markdown", segments: int = 12) -> str:
    ws = workspace or Workspace()
    entry = Library(ws).get(entry_id)
    if entry is None:
        raise ValueError(
            f"no caught episode with id {entry_id!r} — "
            "`openpod list` shows what's in the library"
        )
    transcript = entry.read_transcript()
    if transcript is None:
        raise ValueError(
            f"no transcript for {entry_id!r} — re-run "
            "`openpod catch <link> --transcript <file>` to supply one"
        )
    source = entry.source()

    toc = _briefing.build_toc(transcript, source, segments=segments,
                              structure=entry.read_segments())
    rows = [
        {
            "start": item.start,
            "timestamp": format_timestamp(item.start),
            "moment": theme.moment_plain(item.start),
            "text": item.text,
            "deeplink": item.deeplink or (build_deeplink(source, item.start) if source else None),
        }
        for item in toc
    ]

    if fmt == "json":
        return json.dumps({"entry_id": entry_id, "segments": rows}, indent=2,
                          ensure_ascii=False)

    title = (source.title if source else None) or entry.title()
    lines = [f"# Timestamps — {title}", ""]
    for r in rows:
        stamp = theme.moment_markdown(r["start"], r["deeplink"])
        lines.append(f"- {stamp} — {r['text']}")
    return "\n".join(lines) + "\n"
