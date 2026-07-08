"""The episode-identity crosswalk — resolved once, cached forever.

Episode identity (which YouTube video / Spotify episode / Apple track / RSS
guid are *the same episode*) is a fact about the episode, not the user, so it
is never re-derived per request. Records live under ``.openpod/crosswalk/``:

    crosswalk/
      episodes/<episode_key>.json     # EpisodeIdentity
      shows.json                      # show-level: feed_url <-> platform ids

Show-level facts are written the first time any episode of a show resolves
(or at follow-time) and reused for every future episode of that show.

The schema deliberately contains nothing user-specific so it can later be
served from a shared/global cache without change.
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Optional

from .config import Workspace
from .models import EpisodeIdentity

# Below this, a match is a *candidate* that needs explicit confirmation
# before OpenPod will treat it as ground truth (the #5 gate).
CONFIRM_THRESHOLD = 0.8


class Crosswalk:
    def __init__(self, workspace: Optional[Workspace] = None) -> None:
        self.workspace = workspace or Workspace()

    # -- episode records ----------------------------------------------------- #

    @property
    def _episodes_dir(self):
        return self.workspace.crosswalk_dir / "episodes"

    def get(self, episode_key: str) -> Optional[EpisodeIdentity]:
        path = self._episodes_dir / f"{episode_key}.json"
        if not episode_key or not path.exists():
            return None
        try:
            return EpisodeIdentity.from_dict(
                json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError):
            return None

    def put(self, identity: EpisodeIdentity) -> None:
        if not identity.episode_key:
            return
        self._episodes_dir.mkdir(parents=True, exist_ok=True)
        path = self._episodes_dir / f"{identity.episode_key}.json"
        existing = self.get(identity.episode_key)
        if existing is not None:
            identity = merge(existing, identity)
        path.write_text(json.dumps(identity.to_dict(), indent=2,
                                   ensure_ascii=False), encoding="utf-8")

    def find_by(self, field: str, value: str) -> Optional[EpisodeIdentity]:
        """Linear scan lookup by a platform id (local caches stay small)."""
        if not value or not self._episodes_dir.is_dir():
            return None
        for path in self._episodes_dir.glob("*.json"):
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if d.get(field) == value:
                return EpisodeIdentity.from_dict(d)
        return None

    # -- show-level records --------------------------------------------------- #

    @property
    def _shows_path(self):
        return self.workspace.crosswalk_dir / "shows.json"

    def _load_shows(self) -> dict:
        if not self._shows_path.exists():
            return {}
        try:
            return json.loads(self._shows_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def get_show(self, **ids: str) -> Optional[dict]:
        """Find a show record by any id, e.g. ``get_show(apple_show_id="123")``
        or ``get_show(feed_url=...)``."""
        shows = self._load_shows()
        for record in shows.values():
            if any(record.get(k) == v for k, v in ids.items() if v):
                return record
        return None

    def put_show(self, feed_url: str, **ids) -> dict:
        """Upsert a show record keyed by feed URL; new ids merge in."""
        shows = self._load_shows()
        record = shows.get(feed_url) or {"feed_url": feed_url}
        record.update({k: v for k, v in ids.items() if v is not None})
        shows[feed_url] = record
        self.workspace.crosswalk_dir.mkdir(parents=True, exist_ok=True)
        self._shows_path.write_text(
            json.dumps(shows, indent=2, ensure_ascii=False), encoding="utf-8")
        return record


def merge(base: EpisodeIdentity, update: EpisodeIdentity) -> EpisodeIdentity:
    """Fold ``update`` into ``base`` — new facts fill gaps, provenance merges,
    confidence never silently *drops* an id that was already deterministic."""
    d = base.to_dict()
    for k, v in update.to_dict().items():
        if k in ("methods", "offset_maps"):
            merged = dict(d.get(k) or {})
            merged.update(v)
            d[k] = merged
        elif k == "match_confidence":
            d[k] = min(float(d.get(k, 1.0)), float(v))
        elif d.get(k) in (None, "", 0):
            d[k] = v
    return EpisodeIdentity.from_dict(d)


# --------------------------------------------------------------------------- #
# Match scoring — corroborated, never a single self-reported number
# --------------------------------------------------------------------------- #

_norm_re = re.compile(r"[^a-z0-9 ]+")


def _norm_title(t: str) -> str:
    t = (t or "").lower()
    # Strip common numbering prefixes ("Ep. 42:", "#42 -") so cross-platform
    # title variants of the same episode still compare well.
    t = re.sub(r"^\s*(ep\.?|episode|#)\s*\d+\s*[:\-–—]?\s*", "", t)
    return _norm_re.sub(" ", t).strip()


def title_similarity(a: str, b: str) -> float:
    a, b = _norm_title(a), _norm_title(b)
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def score_match(*, title_a: Optional[str] = None, title_b: Optional[str] = None,
                duration_a: Optional[float] = None,
                duration_b: Optional[float] = None,
                published_a: Optional[str] = None,
                published_b: Optional[str] = None) -> float:
    """Corroborated match confidence in [0, 1].

    Title similarity is the base signal; duration and publish-date proximity
    corroborate or contradict it. A perfect title match with a contradicting
    duration is *penalized* — agreement across independent signals is what
    earns a pass over :data:`CONFIRM_THRESHOLD`.
    """
    score = title_similarity(title_a or "", title_b or "")

    if duration_a and duration_b:
        drift = abs(duration_a - duration_b) / max(duration_a, duration_b)
        if drift <= 0.05:          # within 5% — versions differ by ads/trims
            score = min(1.0, score + 0.1)
        elif drift > 0.25:         # a quarter off — probably not the same
            score *= 0.5

    if published_a and published_b:
        da, db = _date_prefix(published_a), _date_prefix(published_b)
        if da and db:
            if da == db:
                score = min(1.0, score + 0.1)
            elif abs(_ordinal(da) - _ordinal(db)) > 7:
                score *= 0.6

    return round(min(1.0, max(0.0, score)), 3)


_date_re = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_rfc_re = re.compile(r"(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* (\d{4})",
                     re.I)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}


def _date_prefix(s: str) -> Optional[tuple[int, int, int]]:
    """Best-effort (y, m, d) from ISO or RFC-2822 date strings."""
    m = _date_re.search(s or "")
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = _rfc_re.search(s or "")
    if m:
        return int(m.group(3)), _MONTHS[m.group(2).lower()[:3]], int(m.group(1))
    return None


def _ordinal(ymd: tuple[int, int, int]) -> int:
    y, m, d = ymd
    return y * 372 + m * 31 + d   # coarse but monotonic; only used for deltas
