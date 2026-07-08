"""Structured, agent-consumable errors.

Resolution failures are data, not dead ends: each error renders to a dict
with what was tried and what to do next, so a calling agent can branch
programmatically — retry, degrade, ask the user — instead of parsing prose.
"""

from __future__ import annotations

from typing import Any, Optional


class OpenPodError(RuntimeError):
    """Base class; every subclass renders to a structured dict."""

    code = "error"

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": str(self)}


class UnresolvedLinkError(OpenPodError):
    """A link OpenPod could not deterministically classify or resolve.

    Never silently swallowed into a best-effort path — the caller (usually an
    agent) gets the classification attempts and a hint, improvises the tail
    case if it can, and writes what it learns back into the crosswalk.
    """

    code = "unresolved_link"

    def __init__(self, link: str, *, kind: str = "unknown",
                 tried: Optional[list[str]] = None,
                 hint: Optional[str] = None) -> None:
        self.link, self.kind, self.tried = link, kind, tried or []
        self.hint = hint or (
            "Pass kind= explicitly (youtube/podcast/spotify/apple/file), "
            "supply the RSS feed URL, or provide --transcript."
        )
        super().__init__(f"cannot resolve link ({kind}): {link}. {self.hint}")

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "link": self.link, "kind": self.kind,
                "tried": self.tried, "hint": self.hint}


class NeedsConfirmation(OpenPodError):
    """A fuzzy match below the confidence threshold — a candidate, not truth.

    Raised instead of proceeding so a wrong guess can never silently become
    a confidently wrong briefing. Carries everything the interface needs to
    ask "I think this is X — confirm?"; on confirmation the caller re-runs
    with ``confirmed=True`` and the match is recorded as user-confirmed.
    """

    code = "needs_confirmation"

    def __init__(self, candidate: dict, *, confidence: float,
                 method: str, link: Optional[str] = None) -> None:
        self.candidate, self.confidence = candidate, confidence
        self.method, self.link = method, link
        title = candidate.get("title") or "an episode"
        show = candidate.get("show") or "unknown show"
        super().__init__(
            f"matched {title!r} from {show!r} via {method} "
            f"(confidence {confidence:.2f}) — confirm before ingesting"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "candidate": self.candidate,
            "confidence": self.confidence,
            "method": self.method,
            "link": self.link,
            "next_step": (
                "Show the candidate (title, show, date, duration) to the "
                "user and ask them to confirm it is the right episode. On "
                "yes, call catch again with confirmed=true. Never ingest an "
                "unconfirmed fuzzy match."
            ),
        }


class UnsupportedFormatError(OpenPodError):
    """Content whose type OpenPod does not (deliberately) support."""

    code = "unsupported_format"

    def __init__(self, url: str, *, content_type: Optional[str] = None,
                 detail: Optional[str] = None) -> None:
        self.url, self.content_type = url, content_type
        msg = f"unsupported content at {url}"
        if content_type:
            msg += f" (content-type: {content_type})"
        if detail:
            msg += f" — {detail}"
        super().__init__(msg)

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "url": self.url,
                "content_type": self.content_type, "message": str(self)}
