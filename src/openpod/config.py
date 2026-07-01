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
    def persona_file(self) -> Path:
        return self.dot / "persona.md"

    @property
    def follows_file(self) -> Path:
        return self.dot / "follows.yaml"

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
