"""Workspace resolution and the ``.openpod/`` directory layout.

The whole library is a plain directory tree the user owns. OpenPod never syncs
it anywhere. Resolution order for the workspace root:

1. An explicit path passed to :class:`Workspace`.
2. ``$OPENPOD_HOME`` if set.
3. The nearest ancestor directory that already contains ``.openpod/``.
4. The current working directory (a new library is created here on first write).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

DOTDIR = ".openpod"


def _find_existing(start: Path) -> Optional[Path]:
    start = start.resolve()
    for parent in (start, *start.parents):
        if (parent / DOTDIR).is_dir():
            return parent
    return None


class Workspace:
    """Resolves and lays out the ``.openpod/`` tree for a workspace root.

    Paths are computed lazily; nothing is created on disk until
    :meth:`ensure` (or a write through :class:`~openpod.library.Library`).
    """

    def __init__(self, root: Optional[os.PathLike | str] = None) -> None:
        if root is not None:
            self.root = Path(root).expanduser().resolve()
        elif os.environ.get("OPENPOD_HOME"):
            self.root = Path(os.environ["OPENPOD_HOME"]).expanduser().resolve()
        else:
            self.root = _find_existing(Path.cwd()) or Path.cwd().resolve()

    # -- directory layout --------------------------------------------------- #

    @property
    def dot(self) -> Path:
        return self.root / DOTDIR

    @property
    def library_dir(self) -> Path:
        return self.dot / "library"

    @property
    def index_dir(self) -> Path:
        return self.dot / "index"

    @property
    def index_db(self) -> Path:
        return self.index_dir / "search.db"

    @property
    def imports_dir(self) -> Path:
        return self.dot / "imports"

    @property
    def persona_file(self) -> Path:
        return self.dot / "persona.md"

    @property
    def follows_file(self) -> Path:
        return self.dot / "follows.yaml"

    @property
    def crosswalk_dir(self) -> Path:
        """Cached episode-identity records (see :mod:`openpod.crosswalk`)."""
        return self.dot / "crosswalk"

    @property
    def media_dir(self) -> Path:
        """Shared downloaded-media cache — one download per episode, reused
        by ASR, clip, and any external tool."""
        return self.dot / "media"

    @property
    def settings_file(self) -> Path:
        return self.dot / "settings.yaml"

    # -- user settings ------------------------------------------------------- #

    def load_settings(self) -> dict:
        """Read settings.yaml (e.g. ``preferred_playback_app``). Missing file
        or malformed YAML reads as empty — settings are always optional."""
        path = self.settings_file
        if not path.exists():
            return {}
        import yaml

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            return {}
        return data if isinstance(data, dict) else {}

    def save_settings(self, settings: dict) -> None:
        import yaml

        self.dot.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(
            yaml.safe_dump(settings, sort_keys=True, allow_unicode=True),
            encoding="utf-8",
        )

    # -- lifecycle ---------------------------------------------------------- #

    def exists(self) -> bool:
        return self.dot.is_dir()

    def ensure(self) -> "Workspace":
        """Create the base directory structure if missing."""
        self.library_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        return self

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"Workspace(root={self.root})"
