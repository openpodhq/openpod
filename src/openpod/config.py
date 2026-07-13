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

# The documented settings schema (v0). settings.yaml holds only what the
# user changed; every read goes through Workspace.effective_settings() so
# defaults apply uniformly. The load-bearing default: the library clip is
# ALWAYS the clean master — captions/labels are export derivatives, opt-in.
DEFAULT_SETTINGS: dict = {
    "preferred_playback_app": None,     # youtube|spotify|apple|overcast|podcast
    "locale": {
        "preferred_language": None,     # BCP-47, e.g. "he" — captions, briefings
        "fallback_language": "en",
    },
    "asr": {
        # Transient caption failure (429): if the local-ASR high estimate is
        # under this, just transcribe and notify — asking would be slower
        # than doing. Above it, surface the wait-vs-transcribe choice.
        "auto_threshold_seconds": 180,
    },
    "clip": {
        "captions": "off",              # off | soft (sidecar) | burn (export only)
        "caption_language": "preferred",  # preferred | source | explicit code
        "burn_in": False,               # hard subs only on export derivatives
        "style": "karaoke",             # caption chunking/render style
        "label": False,                 # speaker name plate on export
        "label_template": "{name}, {title}",
        "export_dir": None,             # working folder for derivatives/copies
        "keep_clean_master": True,      # the library cut is never mutated
    },
}

_MISSING = object()


def _deep_merge(base: dict, override: dict) -> dict:
    out = {}
    for k, v in base.items():
        o = override.get(k, _MISSING)
        if isinstance(v, dict):
            out[k] = _deep_merge(v, o if isinstance(o, dict) else {})
        else:
            out[k] = v if o is _MISSING else o
    # user keys outside the schema pass through untouched (forward compat)
    for k, v in override.items():
        if k not in base:
            out[k] = v
    return out


def _schema_lookup(dotted_key: str):
    node = DEFAULT_SETTINGS
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _flat_keys(d: dict, prefix: str = "") -> list[str]:
    keys = []
    for k, v in d.items():
        dotted = f"{prefix}{k}"
        if isinstance(v, dict):
            keys.extend(_flat_keys(v, f"{dotted}."))
        else:
            keys.append(dotted)
    return keys


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

    # -- the global (per-user) layer ------------------------------------------ #

    @property
    def global_dot(self) -> Path:
        """``~/.openpod`` — the per-user layer that is true in every
        workspace (global persona; later, the synced account state).
        ``$OPENPOD_GLOBAL_HOME`` overrides it (mainly for tests)."""
        root = os.environ.get("OPENPOD_GLOBAL_HOME")
        return Path(root).expanduser() if root else Path.home() / DOTDIR

    @property
    def global_persona_file(self) -> Path:
        return self.global_dot / "persona.md"

    # -- user settings ------------------------------------------------------- #

    def effective_settings(self) -> dict:
        """settings.yaml deep-merged over :data:`DEFAULT_SETTINGS`.

        This is what feature code reads — every documented key always
        resolves, whether or not the user ever created the file."""
        return _deep_merge(DEFAULT_SETTINGS, self.load_settings())

    def get_setting(self, dotted_key: str):
        """Resolve a dotted key (``clip.captions``) against effective settings."""
        node = self.effective_settings()
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    def set_setting(self, dotted_key: str, value) -> dict:
        """Set a dotted key in settings.yaml (unknown keys are rejected so
        typos surface instead of silently doing nothing)."""
        if _schema_lookup(dotted_key) is _MISSING:
            raise KeyError(
                f"unknown setting {dotted_key!r}; documented keys: "
                + ", ".join(sorted(_flat_keys(DEFAULT_SETTINGS))))
        settings = self.load_settings()
        node = settings
        parts = dotted_key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise ValueError(f"setting {dotted_key!r} conflicts with an "
                                 "existing non-section value")
        node[parts[-1]] = value
        self.save_settings(settings)
        return settings

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
