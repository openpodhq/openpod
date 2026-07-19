"""Podcast RSS ingestion.

Uses ``feedparser`` when available (it handles the messy real-world feeds), and
falls back to a small stdlib ``xml.etree`` parser so the core still works with a
minimal install. Prefers a timed ``podcast:transcript`` when the publisher
provides one; otherwise downloads the enclosure and runs local ASR.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional
from xml.etree import ElementTree as ET

from ..models import SourceRef, Transcript

PODCAST_NS = "https://podcastindex.org/namespace/1.0"
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
_UA = {"User-Agent": "OpenPod/0.1 (+https://github.com/openpodhq/openpod)"}

# Timed transcript mime types, in preference order.
_TIMED_TYPES = ("application/json", "text/vtt", "application/x-subrip", "application/srt")

# Transient HTTP statuses worth a retry (408 request timeout, 425 too-early,
# 429 rate limit, 5xx). Everything else — notably 4xx — fails fast.
_RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass
class FeedItem:
    title: str
    guid: Optional[str]
    published: Optional[str]
    enclosure_url: Optional[str]
    transcript_url: Optional[str]
    transcript_type: Optional[str]
    page_url: Optional[str] = None
    description: Optional[str] = None          # <description> or <itunes:summary>
    duration: Optional[float] = None           # seconds, from <itunes:duration>
    chapters_url: Optional[str] = None         # <podcast:chapters url=...> (JSON)


@dataclass
class Feed:
    title: str
    items: list[FeedItem]


def _fetch(url: str, timeout: int = 30, *,
           retries: int = 2, backoff: float = 0.5) -> bytes:
    """GET ``url`` with a browser-ish UA, retrying transient failures.

    Feeds and podcast CDNs blip — a dropped connection, a read timeout, a
    momentary 503 — and a single miss shouldn't sink an otherwise-fine catch.
    Retries timeouts, connection errors, and retryable HTTP statuses
    (:data:`_RETRY_STATUS`) up to ``retries`` times with exponential backoff.
    A 4xx (bad feed URL, auth wall) is a real answer, so it fails fast.
    """
    req = urllib.request.Request(url, headers=_UA)
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (user-invoked)
                return resp.read()
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code not in _RETRY_STATUS or attempt == retries:
                raise
        except (urllib.error.URLError, TimeoutError) as exc:
            # URLError wraps connection failures; TimeoutError covers read
            # timeouts (socket.timeout is an alias of it on 3.10+).
            last_exc = exc
            if attempt == retries:
                raise
        time.sleep(backoff * (2 ** attempt))
    # The loop always returns or raises above; this is for the type-checker.
    assert last_exc is not None
    raise last_exc


def parse_feed(data: bytes | str) -> Feed:
    """Parse feed bytes/text into a :class:`Feed` (feedparser if present)."""
    try:
        import feedparser  # type: ignore

        parsed = feedparser.parse(data)
        title = parsed.feed.get("title", "podcast")
        items = []
        for e in parsed.entries:
            enc = None
            for link in e.get("links", []):
                if link.get("rel") == "enclosure":
                    enc = link.get("href")
                    break
            enc = enc or (e.enclosures[0].get("href") if e.get("enclosures") else None)
            turl, ttype = _pick_transcript(e.get("podcast_transcript") or e.get("transcript"))
            chap = e.get("podcast_chapters")
            items.append(
                FeedItem(
                    title=e.get("title", "episode"),
                    guid=e.get("id") or e.get("guid"),
                    published=e.get("published"),
                    enclosure_url=enc,
                    transcript_url=turl,
                    transcript_type=ttype,
                    page_url=e.get("link"),
                    description=e.get("summary") or e.get("description"),
                    duration=_parse_duration(e.get("itunes_duration")),
                    chapters_url=(chap.get("url") or chap.get("href")) if isinstance(chap, dict) else None,
                )
            )
        return Feed(title=title, items=items)
    except ImportError:
        return _parse_feed_stdlib(data)


def _pick_transcript(raw) -> tuple[Optional[str], Optional[str]]:
    """Normalise feedparser's transcript field(s) into (url, type)."""
    if not raw:
        return None, None
    entries = raw if isinstance(raw, list) else [raw]
    best = None
    for item in entries:
        if isinstance(item, dict):
            url, typ = item.get("url") or item.get("href"), item.get("type")
        else:
            url, typ = getattr(item, "url", None), getattr(item, "type", None)
        if not url:
            continue
        if typ in _TIMED_TYPES:
            return url, typ  # timed — take immediately
        best = best or (url, typ)
    return best if best else (None, None)


def _parse_duration(raw) -> Optional[float]:
    """``<itunes:duration>`` -> seconds. Accepts ``hh:mm:ss``, ``mm:ss``, or a
    plain number of seconds (the three forms publishers actually use)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    try:
        parts = [float(p) for p in s.split(":")]
    except ValueError:
        return None
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return secs


def _parse_feed_stdlib(data: bytes | str) -> Feed:
    if isinstance(data, str):
        data = data.encode("utf-8")
    root = ET.fromstring(data)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("not an RSS feed (no <channel>)")
    title = (channel.findtext("title") or "podcast").strip()
    items = []
    for item in channel.findall("item"):
        enc = item.find("enclosure")
        enc_url = enc.get("url") if enc is not None else None
        turl = ttype = None
        best = None
        for tr in item.findall(f"{{{PODCAST_NS}}}transcript"):
            url, typ = tr.get("url"), tr.get("type")
            if not url:
                continue
            if typ in _TIMED_TYPES:
                turl, ttype = url, typ
                break
            best = best or (url, typ)
        if turl is None and best:
            turl, ttype = best
        desc = (item.findtext("description")
                or item.findtext(f"{{{ITUNES_NS}}}summary") or "").strip() or None
        chap = item.find(f"{{{PODCAST_NS}}}chapters")
        items.append(
            FeedItem(
                title=(item.findtext("title") or "episode").strip(),
                guid=(item.findtext("guid") or "").strip() or None,
                published=(item.findtext("pubDate") or "").strip() or None,
                enclosure_url=enc_url,
                transcript_url=turl,
                transcript_type=ttype,
                page_url=(item.findtext("link") or "").strip() or None,
                description=desc,
                duration=_parse_duration(item.findtext(f"{{{ITUNES_NS}}}duration")),
                chapters_url=chap.get("url") if chap is not None else None,
            )
        )
    return Feed(title=title, items=items)


def load_feed(url: str) -> Feed:
    return parse_feed(_fetch(url))


def _fetch_chapters(url: str) -> list[dict]:
    """Fetch + parse a Podcasting 2.0 ``<podcast:chapters>`` JSON file into the
    ``{start, title, end?}`` shape the segment layer consumes.

    Best-effort: any network or parse failure returns ``[]``. Creator chapters
    are a nice-to-have anchor, never a reason to fail a catch.
    """
    try:
        data = json.loads(_fetch(url, retries=1).decode("utf-8", "replace"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    out: list[dict] = []
    for ch in (data.get("chapters") or []):
        if not isinstance(ch, dict):
            continue
        start = ch.get("startTime")
        if start is None:
            continue
        entry: dict = {"start": float(start),
                       "title": (ch.get("title") or "").strip() or None}
        if ch.get("endTime") is not None:
            entry["end"] = float(ch["endTime"])
        out.append(entry)
    return out


def _chapters_for(item: FeedItem) -> list[dict]:
    """Creator chapters for an episode: the Podcasting 2.0 chapters file when
    the feed links one, else timestamp lines parsed from the show notes (same
    rules YouTube uses). Returns ``[]`` when neither yields structure."""
    if item.chapters_url:
        chapters = _fetch_chapters(item.chapters_url)
        if chapters:
            return chapters
    if item.description:
        from ..segments import parse_description_chapters
        return parse_description_chapters(item.description, item.duration)
    return []


def ingest_podcast(link: str, *, item: FeedItem | None = None,
                   feed_title: str | None = None,
                   progress=None) -> tuple[SourceRef, Transcript]:
    """Resolve a podcast link (feed URL, or direct enclosure) to (source, transcript)."""
    from .. import transcript as tx

    if item is None:
        # A feed URL: take the most recent episode.
        feed = load_feed(link)
        if not feed.items:
            raise ValueError(f"feed has no episodes: {link}")
        item = feed.items[0]
        feed_title = feed.title

    source = SourceRef(
        kind="podcast",
        url=item.page_url or link,
        show=feed_title,
        title=item.title,
        guid=item.guid,
        published=item.published,
        audio_url=item.enclosure_url,
        duration=item.duration,
    )
    # Creator chapters straight from the feed — sharpens the ASR estimate
    # (source.duration) and gives deep-links a chapter anchor without a
    # second platform round-trip. Best-effort; absence is fine.
    chapters = _chapters_for(item)
    if chapters:
        source.chapters = chapters

    # Prefer a publisher transcript (timed form).
    if item.transcript_url:
        raw = _fetch(item.transcript_url).decode("utf-8", "replace")
        fmt = _fmt_for_type(item.transcript_type, item.transcript_url)
        try:
            return source, tx.load(raw, fmt=fmt, source="podcast:transcript")
        except ValueError:
            pass  # untimed/HTML transcript — fall through to ASR

    # No timed transcript: validate, download the enclosure, run local ASR.
    # Feeds list whatever they like — the enclosure gets a content-type check
    # before anything is piped into transcription (explicit rejection beats
    # incidental library tolerance).
    if not item.enclosure_url:
        raise ValueError("no transcript and no audio enclosure for this episode")
    from ..asr import estimate_transcription, transcribe, download_audio
    from .validate import ensure_media

    if progress:
        est = estimate_transcription(source.duration)
        progress("no publisher transcript for this episode — downloading the "
                 f"audio and transcribing locally ({est.get('human', 'several minutes')})")
    info = ensure_media(item.enclosure_url)
    audio = download_audio(info.url)
    t = transcribe(audio)
    t.notes = "no publisher transcript; transcribed locally"
    return source, t


def _fmt_for_type(mime: Optional[str], url: str) -> Optional[str]:
    if mime:
        if "json" in mime:
            return "json3"
        if "vtt" in mime:
            return "vtt"
        if "srt" in mime or "subrip" in mime:
            return "srt"
    lower = url.lower()
    for ext, fmt in ((".vtt", "vtt"), (".srt", "srt"), (".json", "json3")):
        if lower.endswith(ext):
            return fmt
    return None
