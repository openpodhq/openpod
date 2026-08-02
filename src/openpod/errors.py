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


class NeedsDecision(OpenPodError):
    """Burning pixels on an unconfigured workspace is the user's call.

    Raised when a burn is requested before first-use setup has run: the
    setup questions ship in the payload; the interface presents them to the
    user, saves the answers via settings (plus ``clip.setup_done=true``),
    and re-runs. Only the user may skip — skipping keeps defaults.

    A refusal, not an advisory: field testing showed weaker models ignore
    an advisory ``first_use`` block attached to a successful result and
    ship the defaults, but every model reads a tool call that returns no
    file."""

    code = "needs_decision"

    def __init__(self, questions: dict) -> None:
        self.questions = questions
        super().__init__(
            "first clip in this workspace and the request would burn pixels "
            "— the caption/export choices belong to the user. Present the "
            "first_use questions, save the answers, then re-run."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "first_use": self.questions,
            "next_step": (
                "These are the user's decisions, not yours. Present the "
                "questions ONCE as one multiple-choice message, save each "
                "answer with the settings tool, set clip.setup_done=true, "
                "and re-run this exact call. If the user explicitly skips, "
                "set clip.setup_done=true and re-run with defaults. Never "
                "answer on the user's behalf."
            ),
        }


class CaptionsUnavailable(OpenPodError):
    """Captions failed and falling back to local ASR is a *decision*, not
    a default.

    Raised for **transient** failures (YouTube 429 / IP throttling) where
    waiting and re-catching would get free, instant captions — instead of
    silently burning minutes of local Whisper. Carries the ASR time estimate
    so the interface can offer both options honestly: "wait and retry" vs
    "transcribe locally now (~N min)".
    """

    code = "captions_unavailable"

    def __init__(self, link: str, *, reason: str, detail: Optional[str] = None,
                 estimate: Optional[dict] = None) -> None:
        self.link, self.reason, self.detail = link, reason, detail
        self.estimate = estimate or {}
        human = self.estimate.get("human", "several minutes")
        super().__init__(
            f"captions unavailable ({reason}) for {link}. "
            f"Wait and re-catch to get captions, or re-run with asr='now' "
            f"to transcribe locally (~{human})."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "link": self.link,
            "reason": self.reason,          # "rate_limited" | "transient"
            "detail": self.detail,
            "asr_estimate": self.estimate,
            "options": [
                {"option": "wait_and_recatch",
                 "description": "Rate limits are temporary — re-run catch in "
                                "a few minutes and captions are free and "
                                "instant (a re-catch replaces the transcript)."},
                {"option": "asr_now",
                 "description": "Call catch again with asr='now' to "
                                "transcribe locally with Whisper — "
                                f"~{self.estimate.get('human', 'several minutes')} "
                                "on this machine."},
            ],
            "next_step": (
                "Tell the user captions are temporarily unavailable and give "
                "them the choice: wait and re-catch (free, exact platform "
                "timing) or start local transcription now with the time "
                "estimate. Never start a long ASR run without telling them."
            ),
        }


class WorkspaceMismatchError(OpenPodError):
    """``$OPENPOD_HOME`` and the current directory name different libraries.

    Raised instead of writing: a stale ``OPENPOD_HOME`` in a shell profile
    once routed three agents' catches into the production library while
    each sat inside its own freshly-initialized test workspace — silently,
    because the env var wins resolution and nothing pointed at the loser.
    Reads survive with a warning; a write must say which library it is for
    via an explicit ``--home``.
    """

    code = "workspace_mismatch"

    def __init__(self, env_root, cwd_root) -> None:
        self.env_root, self.cwd_root = str(env_root), str(cwd_root)
        super().__init__(
            f"refusing to write: $OPENPOD_HOME points at {self.env_root} "
            f"but the current directory belongs to a different workspace "
            f"at {self.cwd_root}. Re-run with --home <path> to say which "
            f"library this write is for."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "env_root": self.env_root,
            "cwd_root": self.cwd_root,
            "options": [
                {"option": "use_cwd_workspace",
                 "description": "The library in the current directory is "
                                "the target — re-run with "
                                f"--home {self.cwd_root}"},
                {"option": "use_env_workspace",
                 "description": "The $OPENPOD_HOME library really is the "
                                "target — re-run with "
                                f"--home {self.env_root}"},
            ],
            "next_step": (
                "Two different libraries are in play: $OPENPOD_HOME names "
                "one, the current directory belongs to another. If it is "
                "not obvious which the user means, ask — then re-run with "
                "an explicit --home. Never guess: a write into the wrong "
                "library is silent and expensive to undo."
            ),
        }


class InitNeedsExplicitHome(OpenPodError):
    """``openpod init`` with ``$OPENPOD_HOME`` set must say where.

    The mismatch guard cannot protect init in a fresh directory — there is
    no rival ``.openpod/`` yet, just an env var silently redirecting the
    new workspace somewhere the caller cannot see. That is exactly how the
    field incident's half-made test workspaces happened. So init refuses
    whenever the env var would pick the target: an explicit ``--home`` is
    the only way to create a workspace while ``$OPENPOD_HOME`` is set
    (unless the current directory already lies inside that workspace, where
    there is nothing to disambiguate).
    """

    code = "init_needs_home"

    def __init__(self, env_root, cwd) -> None:
        self.env_root, self.cwd = str(env_root), str(cwd)
        super().__init__(
            f"refusing to init: $OPENPOD_HOME is set ({self.env_root}) and "
            f"would silently pick where the workspace lands. Re-run with "
            f"--home <path> to say where — e.g. --home {self.cwd} for the "
            f"current directory."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "env_root": self.env_root,
            "cwd": self.cwd,
            "options": [
                {"option": "init_here",
                 "description": "Create the workspace in the current "
                                f"directory — re-run with --home {self.cwd}"},
                {"option": "init_env_workspace",
                 "description": "Initialize the $OPENPOD_HOME library — "
                                f"re-run with --home {self.env_root}"},
            ],
            "next_step": (
                "$OPENPOD_HOME would decide where this new workspace lands, "
                "and that redirect is invisible. If it is not obvious which "
                "location the user means, ask — then re-run init with an "
                "explicit --home."
            ),
        }


class TranscriptUnavailable(OpenPodError):
    """The feed ships no timed transcript and local ASR is a *decision*,
    not a default.

    The podcast twin of :class:`CaptionsUnavailable`, with one honest
    difference: waiting cannot help — a feed with no transcript today will
    not grow one by re-catching. The real options are to transcribe locally
    now (priced), or to catch a different source carrying the same episode
    (e.g. its YouTube link, where captions are free and instant — the
    crosswalk records the identity, and output links still render on the
    origin platform).
    """

    code = "transcript_unavailable"

    def __init__(self, link: str, *, show: Optional[str] = None,
                 title: Optional[str] = None,
                 estimate: Optional[dict] = None) -> None:
        self.link, self.show, self.title = link, show, title
        self.estimate = estimate or {}
        human = self.estimate.get("human", "several minutes")
        what = repr(title) if title else "this episode"
        super().__init__(
            f"the feed has no transcript for {what}. Re-run with asr='now' "
            f"to transcribe locally (~{human}), or catch a different source "
            "for the same episode (e.g. a YouTube link with captions)."
        )

    def to_dict(self) -> dict[str, Any]:
        human = self.estimate.get("human", "several minutes")
        return {
            "error": self.code,
            "link": self.link,
            "show": self.show,
            "title": self.title,
            "asr_estimate": self.estimate,
            "options": [
                {"option": "asr_now",
                 "description": "Call catch again with asr='now' to "
                                "transcribe locally with Whisper — "
                                f"~{human} on this machine, no tokens or "
                                "API cost."},
                {"option": "alternate_source",
                 "description": "Catch a different link carrying the SAME "
                                "episode — e.g. its YouTube URL, where "
                                "captions are free and instant. The "
                                "crosswalk records the identity either way, "
                                "so output links still render on the user's "
                                "platform."},
            ],
            "next_step": (
                "Tell the user this feed ships no transcript and give them "
                "the choice: transcribe locally now (quote the estimate) or "
                "supply another source for the episode (a YouTube link with "
                "captions is instant). Never start a long ASR run without "
                "telling them."
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
