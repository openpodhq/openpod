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

INTERVIEW_QUESTIONS = [
    "What is your role, and what are you responsible for day to day?",
    "What are the 2–3 projects or problems you're actively working on right now?",
    "Which topics do you want to keep up with for work? Which do you want to filter out?",
    "Whose podcasts / channels do you trust most, and why?",
    "When you listen to something long, what are you usually hoping to extract?",
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
<!-- Topics to skip or compress hard. -->

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
        if self.exists():
            self._replace_derived(block)
        else:
            self.init()
            self._replace_derived(block)
        return block

    def _replace_derived(self, block: str) -> None:
        text = self.read() or _TEMPLATE
        marker = "## Derived from my library"
        head = text.split(marker)[0].rstrip()
        self.path.write_text(f"{head}\n\n{marker}\n\n{block}\n", encoding="utf-8")


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
