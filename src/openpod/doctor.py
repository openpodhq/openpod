"""``openpod doctor`` — capability report + library hygiene.

Two QA findings productized:

* **Capability**: builds of ffmpeg routinely lack libass/drawtext; anything
  promising burned-in text must know that *before* promising (QA-08).
* **Hygiene**: the library is the durable personal corpus. Venvs, one-off
  scripts, overlay PNGs, and preview stills that agents drop next to clips
  are contamination (QA-05). ``doctor`` names them; ``--fix`` quarantines
  them under ``library/_scratch/`` (moved, never deleted — the user decides).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from .config import Workspace

# What openpod primitives actually write into an entry directory.
_ENTRY_FILES = {"meta.json", "transcript.json", "briefing.md", "ideas.md",
                "notes.md", "listened.json"}
_ENTRY_DIRS = {"clips"}
# ...and into clips/: media + metadata + card + caption sidecars.
_CLIP_SUFFIXES = {".mp3", ".m4a", ".aac", ".ogg", ".wav", ".flac",
                  ".mp4", ".mkv", ".webm", ".mov",
                  ".json", ".html", ".png", ".srt", ".vtt"}
SCRATCH_DIR = "_scratch"


def check(workspace: Optional[Workspace] = None, *, fix: bool = False) -> dict:
    """Run all checks; with ``fix=True`` also quarantine foreign files."""
    ws = workspace or Workspace()
    report = {
        "workspace": str(ws.root),
        "ffmpeg": _ffmpeg_report(),
        "settings": _settings_report(ws),
        "library": _hygiene_report(ws, fix=fix),
    }
    return report


def _ffmpeg_report() -> dict:
    from .asr import ffmpeg_capabilities

    caps = ffmpeg_capabilities()
    notes = []
    if not caps["ffmpeg"]:
        notes.append("ffmpeg not on PATH — clip and burn-in unavailable")
    else:
        if not caps["subtitles"]:
            notes.append("no subtitles filter (libass): caption burn-in "
                         "unavailable; soft sidecars (.srt/.vtt) still work")
        if not caps["drawtext"]:
            notes.append("no drawtext filter: label burn-in unavailable")
    return {**caps, "notes": notes}


def _settings_report(ws: Workspace) -> dict:
    exists = ws.settings_file.exists()
    effective = ws.effective_settings()
    return {
        "path": str(ws.settings_file),
        "exists": exists,
        "effective": effective,
        "notes": [] if exists else [
            "no settings.yaml yet — defaults apply; set e.g. "
            "locale.preferred_language and clip.export_dir to match your workflow"
        ],
    }


def _is_foreign_clip_file(p: Path) -> bool:
    if p.name.startswith("."):
        return True
    if p.is_dir():
        return True                     # venvs, overlay dirs — never ours
    if p.suffix.lower() not in _CLIP_SUFFIXES:
        return True
    # Ours are media/metadata; scripts dressed as allowed types aren't a
    # thing (we write no .py), so suffix check is enough.
    return False


def _hygiene_report(ws: Workspace, *, fix: bool) -> dict:
    base = ws.library_dir
    foreign: list[dict] = []
    if base.is_dir():
        for show_dir in base.iterdir():
            if not show_dir.is_dir() or show_dir.name == SCRATCH_DIR:
                continue
            for ep_dir in (p for p in show_dir.iterdir() if p.is_dir()):
                for item in ep_dir.iterdir():
                    if item.is_dir() and item.name in _ENTRY_DIRS:
                        for c in item.iterdir():
                            if _is_foreign_clip_file(c):
                                foreign.append(_foreign(c, ws, fix))
                    elif item.is_file() and item.name not in _ENTRY_FILES:
                        foreign.append(_foreign(item, ws, fix))
                    elif item.is_dir() and item.name not in _ENTRY_DIRS:
                        foreign.append(_foreign(item, ws, fix))
    notes = []
    if foreign and not fix:
        notes.append(
            f"{len(foreign)} foreign item(s) in the library — the corpus "
            "should hold only openpod artifacts. Re-run with --fix to "
            f"quarantine them under library/{SCRATCH_DIR}/ (moved, not deleted). "
            "Tooling belongs in a working folder (clip.export_dir), not the vault.")
    return {"foreign": foreign, "quarantined": fix, "notes": notes}


def _foreign(path: Path, ws: Workspace, fix: bool) -> dict:
    rel = path.relative_to(ws.library_dir)
    entry = {"path": str(rel), "kind": "dir" if path.is_dir() else "file"}
    if fix:
        dest = ws.library_dir / SCRATCH_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))
        entry["moved_to"] = str(dest.relative_to(ws.library_dir))
    return entry
