"""Local, evolving persona.

``persona.md`` is a **user-owned workspace file the agent reads** — never an
OpenPod-hosted profile. A skill interviews the user and researches their
workspace to seed it; it then re-derives from the growing library (what was
saved, clipped, asked, flagged) so it sharpens with use. Stage 1 evolves it from
*explicit* signal only; behavioural signal (replay/skip) is Stage 2.
"""

from __future__ import annotations

from collections import Counter
from typing import Optional

from .briefing import _tokens
from .config import Workspace
from .library import Library

# The two machine-owned persona blocks. Everything above/outside them is the
# human's voice and is never rewritten by OpenPod code (Library UI spec §2.2:
# "`persona derive` is idempotent and non-destructive above the marker").
DERIVED_MARKER = "## Derived from my library"
IMPORTED_MARKER = "## Imported interests (opt-in)"
MACHINE_MARKERS = (IMPORTED_MARKER, DERIVED_MARKER)

# The interview is guess-and-confirm, not a form. Before asking anything, the
# agent runs the workspace evidence scan (`openpod persona scan` / the
# `persona_scan` tool) and presents each question as **multi-select options
# mined from that evidence**, with a free-text escape hatch. The user's job is
# to pick, not to type. ("Whose podcasts do you trust" was retired: the
# follows list and OPML import already answer it better than a question can.)
INTERVIEW_QUESTIONS = [
    "Role — what are you responsible for? (guess from the workspace: repos, "
    "docs, CLAUDE.md; offer options like founder / PM / engineer / analyst)",
    "Current projects — which of these are you actively working on? "
    "(multi-select from scanned project folders and recent docs)",
    "Topics to amplify — which of these should briefings go deep on, and "
    "what angle? (multi-select from doc titles, library themes, follows; "
    "suggest an angle per topic, e.g. 'OSS strategy → licensing & launch "
    "playbooks')",
    "Topics to filter — which of these should be compressed or skipped? "
    "(multi-select; offer plausible noise for this user, not a blank box)",
    "What to extract from long-form — decisions, named tools, numbers, "
    "frameworks, contrarian takes, market signals, the 5 minutes that "
    "change your mind? (multi-select)",
]

_TEMPLATE = """\
# Persona

_A local, user-owned file your AI agent reads to personalize briefings and
triage. Edit it freely. OpenPod never uploads it._

## Role
<!-- Who you are and what you're responsible for. -->

## Current projects
<!-- 2–3 things you're actively working on; briefings get tuned to these. -->

## Interests (amplify)
<!-- Topics to surface and go deep on. -->

## Not interested (filter)
<!-- Topics to skip or compress hard. Topics, not shows: an unfamiliar
     source is discovery, not noise — filtering happens on what's said,
     never on who says it. -->

## What I want from long-form
<!-- e.g. "the 5 minutes that change my mind", decisions, named tools, numbers. -->

## Derived from my library
<!-- Auto-refreshed by `openpod persona derive`. -->
"""


class Persona:
    def __init__(self, workspace: Optional[Workspace] = None) -> None:
        self.workspace = workspace or Workspace()

    @property
    def path(self):
        return self.workspace.persona_file

    def exists(self) -> bool:
        return self.path.exists()

    def read(self) -> Optional[str]:
        return self.path.read_text(encoding="utf-8") if self.exists() else None

    def init(self, *, force: bool = False) -> str:
        """Write the persona template if missing. Returns the file path."""
        self.workspace.dot.mkdir(parents=True, exist_ok=True)
        if self.exists() and not force:
            return str(self.path)
        self.path.write_text(_TEMPLATE, encoding="utf-8")
        return str(self.path)

    def interview(self) -> list[str]:
        """The questions a persona skill asks. (The agent runs the interview.)"""
        return list(INTERVIEW_QUESTIONS)

    def derive(self, *, top_terms: int = 20) -> str:
        """Summarize what the library reveals and refresh the derived section."""
        library = Library(self.workspace)
        shows: Counter[str] = Counter()
        terms: Counter[str] = Counter()
        n_episodes = 0
        for entry in library:
            n_episodes += 1
            shows[entry.show()] += 1
            transcript = entry.read_transcript()
            if transcript:
                terms.update(_tokens(transcript.text()))

        block = _render_derived(n_episodes, shows, terms, top_terms)
        self.replace_section(DERIVED_MARKER, block)
        return block

    def replace_section(self, marker: str, block: str) -> None:
        """Replace the body of one machine-owned section, byte-preserving
        everything else — the human sections and the other machine block."""
        if marker not in MACHINE_MARKERS:
            raise ValueError(f"not a machine-owned persona section: {marker!r}")
        if not self.exists():
            self.init()
        text = self.read() or _TEMPLATE
        self.path.write_text(_splice_section(text, marker, block),
                             encoding="utf-8")


def _splice_section(text: str, marker: str, block: str) -> str:
    """Return ``text`` with the section under ``marker`` replaced by ``block``.

    The section body runs from the marker line to the next ``## `` heading (or
    EOF). If the marker is missing it is inserted: ``IMPORTED_MARKER`` goes just
    above ``DERIVED_MARKER`` when present, otherwise the section is appended.
    Everything outside the section is preserved verbatim.
    """
    lines = text.splitlines()
    body = ["", *block.rstrip("\n").splitlines(), ""]

    start = next((i for i, l in enumerate(lines) if l.strip() == marker), None)
    if start is not None:
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].startswith("## ")), len(lines))
        lines = lines[:start + 1] + body + lines[end:]
    else:
        insert_at = len(lines)
        if marker == IMPORTED_MARKER:
            derived = next((i for i, l in enumerate(lines)
                            if l.strip() == DERIVED_MARKER), None)
            if derived is not None:
                insert_at = derived
        lines = lines[:insert_at] + [marker] + body + lines[insert_at:]

    return "\n".join(lines).rstrip("\n") + "\n"


def _render_derived(n_episodes: int, shows: Counter, terms: Counter,
                    top_terms: int) -> str:
    if n_episodes == 0:
        return "_No episodes caught yet — this fills in as you use OpenPod._"
    top_shows = ", ".join(f"{s} ({c})" for s, c in shows.most_common(5))
    top_words = ", ".join(w for w, _ in terms.most_common(top_terms))
    return (
        f"- **Episodes caught:** {n_episodes}\n"
        f"- **Most-followed shows:** {top_shows}\n"
        f"- **Recurring themes:** {top_words}\n"
    )
