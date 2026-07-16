"""Podcast RSS ingestion.

Uses ``feedparser`` when available (it handles the messy real-world feeds), and
falls back to a small stdlib ``xml.etree`` parser so the core still works with a
minimal install. Prefers a timed ``podcast:transcript`` when the publisher
provides one; otherwise downloads the enclosure and runs local ASR.
"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from typing import Optional
from xml.etree import ElementTree as ET

from ..models import SourceRef, Transcript

PODCAST_NS = "https://podcastindex.org/namespace/1.0"
_UA = {"User-Agent": "OpenPod/0.1 (+https://github.com/openpodhq/openpod)"}

# Timed transcript mime types, in preference order.
_TIMED_TYPES = ("application/json", "text/vtt", "application/x-subrip", "application/srt")


@dataclass
class FeedItem:
    title: str
    guid: Optional[str]
    published: Optional[str]
    enclosure_url: Optional[str]
    transcript_url: Optional[str]
    transcript_type: Optional[str]
    page_url: Optional[str] = None


@dataclass
class Feed:
    title: str
    items: list[FeedItem]


def _fetch(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (user-invoked)
        return resp.read()


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
            items.append(
                FeedItem(
                    title=e.get("title", "episode"),
                    guid=e.get("id") or e.get("guid"),
                    published=e.get("published"),
                    enclosure_url=enc,
                    transcript_url=turl,
                    transcript_type=ttype,
                    page_url=e.get("link"),
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
        items.append(
            FeedItem(
                title=(item.findtext("title") or "episode").strip(),
                guid=(item.findtext("guid") or "").strip() or None,
                published=(item.findtext("pubDate") or "").strip() or None,
                enclosure_url=enc_url,
                transcript_url=turl,
                transcript_type=ttype,
                page_url=(item.findtext("link") or "").strip() or None,
            )
        )
    return Feed(title=title, items=items)


def load_feed(url: str) -> Feed:
    return parse_feed(_fetch(url))


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
    )

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
