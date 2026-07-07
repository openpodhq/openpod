"""Follow list + local discovery digest.

"Follow" in Stage 1 is a **local list, not a sync**. ``follows.yaml`` holds
podcast RSS URLs and YouTube channels; polling it locally yields the "what's new
in your follows" digest. Cross-device sync of this list is the lightest, highest
-value Stage 2 entry point — but it's a local default here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlparse

from .config import Workspace
from .ingest.resolve import detect_kind

YT_CHANNEL_RSS = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"


@dataclass
class Follow:
    url: str
    kind: str
    title: Optional[str] = None
    # Provenance: where this follow came from, e.g. "opml:overcast". Absent for
    # follows the user added by hand, so imports stay visible and trimmable.
    source: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in {"url": self.url, "kind": self.kind,
                                  "title": self.title,
                                  "source": self.source}.items() if v}


@dataclass
class DigestItem:
    show: str
    title: str
    link: Optional[str]
    published: Optional[str]
    kind: str
    # False when the user follows this show but has never caught an episode
    # of it — the unlistened tail. That's the discovery pool: content worth
    # sampling *because* it's outside the rotation, not despite it.
    in_rotation: bool = False


class Follows:
    def __init__(self, workspace: Optional[Workspace] = None) -> None:
        self.workspace = workspace or Workspace()

    # -- persistence -------------------------------------------------------- #

    def _load(self) -> list[Follow]:
        path = self.workspace.follows_file
        if not path.exists():
            return []
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        return [Follow(**item) for item in data]

    def _save(self, follows: list[Follow]) -> None:
        import yaml

        self.workspace.dot.mkdir(parents=True, exist_ok=True)
        self.workspace.follows_file.write_text(
            yaml.safe_dump([f.to_dict() for f in follows], sort_keys=False,
                           allow_unicode=True),
            encoding="utf-8",
        )

    def list(self) -> list[Follow]:
        return self._load()

    # -- mutations ---------------------------------------------------------- #

    def add(self, url: str, *, title: Optional[str] = None,
            source: Optional[str] = None) -> Follow:
        url = _normalize(url)
        follows = self._load()
        if any(f.url == url for f in follows):
            return next(f for f in follows if f.url == url)
        follow = Follow(url=url, kind=_follow_kind(url), title=title,
                        source=source)
        follows.append(follow)
        self._save(follows)
        return follow

    def remove(self, url: str) -> bool:
        url = _normalize(url)
        follows = self._load()
        kept = [f for f in follows if f.url != url]
        if len(kept) == len(follows):
            return False
        self._save(kept)
        return True

    # -- discovery ---------------------------------------------------------- #

    def poll(self, *, per_feed: int = 5) -> list[DigestItem]:
        """Fetch recent items across all follows. Best-effort; skips failures.

        Each item is marked ``in_rotation`` by whether the user has ever
        caught an episode of that show, so a digest can present "your
        rotation" and "new to you" separately instead of letting familiarity
        rank everything."""
        from .ingest import rss
        from .library import Library
        from .models import slugify

        caught_shows = {e.show_slug for e in Library(self.workspace)}
        items: list[DigestItem] = []
        for follow in self._load():
            try:
                feed = rss.load_feed(follow.url)
            except Exception:
                continue
            show = follow.title or feed.title
            in_rotation = slugify(show, fallback="show") in caught_shows
            for it in feed.items[:per_feed]:
                items.append(DigestItem(
                    show=show, title=it.title,
                    link=it.page_url or it.enclosure_url,
                    published=it.published, kind=follow.kind,
                    in_rotation=in_rotation,
                ))
        return items


def _normalize(url: str) -> str:
    """Turn a YouTube channel URL into its RSS feed; pass others through."""
    u = urlparse(url)
    if "youtube.com" in u.netloc.lower():
        qs = parse_qs(u.query)
        if "channel_id" in qs:
            return YT_CHANNEL_RSS.format(cid=qs["channel_id"][0])
        parts = [p for p in u.path.split("/") if p]
        if len(parts) == 2 and parts[0] == "channel":
            return YT_CHANNEL_RSS.format(cid=parts[1])
    return url


def _follow_kind(url: str) -> str:
    if "youtube.com/feeds/videos.xml" in url:
        return "youtube"
    kind = detect_kind(url)
    return kind if kind in ("youtube", "podcast") else "podcast"
