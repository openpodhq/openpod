"""Local, evolving persona — two layers.

Two different kinds of facts, two files:

* **Global** (``~/.openpod/persona.md``): *who you are* — role, language,
  briefing style, standing filters. True in every workspace; the layer a
  cloud tier would sync.
* **Workspace** (``.openpod/persona.md``): *what this folder is for* — the
  project's focus, what "relevant" means here, plus the machine-derived
  blocks (which are derived from *this* library, so they belong here).

Both are **user-owned files the agent reads** — never an OpenPod-hosted
profile. Reads return both layers labeled; the agent interprets (identity
and language lean global, relevance decisions lean workspace). Setup
follows the house interaction rule: the user picks from options mined from
evidence — they never elaborate into a blank box. Stage 1 evolves the
persona from *explicit* signal only; behavioural signal (replay/skip) is
Stage 2.
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
# Persona — this workspace

_What this folder is about. Your AI agent reads it to decide what's
relevant **here**; who you are globally lives in ~/.openpod/persona.md.
Edit freely. OpenPod never uploads it._

## Current projects
<!-- 2–3 things this workspace is actively about; briefings tune to these. -->

## Interests (amplify)
<!-- Topics to surface and go deep on — in this workspace. -->

## Not interested (filter)
<!-- Topics to skip or compress hard — here. Topics, not shows: an
     unfamiliar source is discovery, not noise — filtering happens on
     what's said, never on who says it. -->

## Derived from my library
<!-- Auto-refreshed by `openpod persona derive`. -->
"""

_GLOBAL_TEMPLATE = """\
# Persona — global

_Who you are, in every workspace: role, language, how you like briefings.
A local, user-owned file; a paid sync tier may later carry it across
devices — it is never required to. Edit freely._

## Role
<!-- Who you are and what you're responsible for. -->

## Language & style
<!-- Preferred language for briefings/captions; how you like things written. -->

## Standing interests
<!-- Topics you always care about, regardless of project. -->

## Standing filters
<!-- Topics to always skip or compress, everywhere. -->

## What I want from long-form
<!-- e.g. "the 5 minutes that change my mind", decisions, named tools, numbers. -->
"""

# Headings that describe the *person* (global layer) vs the *folder*
# (workspace layer). propose_split() classifies existing sections with
# these; anything unrecognized goes into the "ask the user" bucket —
# always as a multiple-choice pick, never a writing exercise.
GLOBAL_SECTIONS = {"role", "language & style", "language", "style",
                   "standing interests", "standing filters",
                   "what i want from long-form"}
WORKSPACE_SECTIONS = {"current projects", "interests (amplify)",
                      "not interested (filter)",
                      "derived from my library",
                      "imported interests (opt-in)"}


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

    # -- the global layer ----------------------------------------------------- #

    @property
    def global_path(self):
        return self.workspace.global_persona_file

    def global_exists(self) -> bool:
        return self.global_path.exists()

    def read_global(self) -> Optional[str]:
        return (self.global_path.read_text(encoding="utf-8")
                if self.global_exists() else None)

    def init_global(self, *, force: bool = False) -> str:
        self.global_path.parent.mkdir(parents=True, exist_ok=True)
        if self.global_exists() and not force:
            return str(self.global_path)
        self.global_path.write_text(_GLOBAL_TEMPLATE, encoding="utf-8")
        return str(self.global_path)

    def read_layers(self) -> dict:
        """Both layers, labeled — the agent interprets precedence (identity
        and language lean global; relevance decisions lean workspace)."""
        return {
            "global": {"path": str(self.global_path),
                       "exists": self.global_exists(),
                       "content": self.read_global()},
            "workspace": {"path": str(self.path),
                          "exists": self.exists(),
                          "content": self.read()},
        }

    # -- splitting an existing single-layer persona ---------------------------- #

    def propose_split(self) -> Optional[dict]:
        """When a workspace persona predates the global layer, propose which
        sections belong to the person vs the folder.

        Returns None when there's nothing to split (no workspace persona, or
        global already exists). The proposal is multiple-choice material:
        the user picks from ``proposed_global`` / ``unclassified`` — they
        never have to write anything. Machine-owned sections are never
        offered."""
        if self.global_exists() or not self.exists():
            return None
        sections = _sections(self.read() or "")
        proposal = {"proposed_global": [], "stays_local": [], "unclassified": []}
        for heading, body in sections:
            key = heading.strip().lower()
            has_content = any(l.strip() and not l.strip().startswith("<!--")
                              for l in body.splitlines())
            item = {"section": heading, "excerpt": body.strip()[:200],
                    "empty": not has_content}
            if heading in [m.lstrip("# ").strip() for m in MACHINE_MARKERS] \
                    or key in WORKSPACE_SECTIONS:
                proposal["stays_local"].append(item)
            elif key in GLOBAL_SECTIONS:
                proposal["proposed_global"].append(item)
            else:
                proposal["unclassified"].append(item)
        if not proposal["proposed_global"] and not proposal["unclassified"]:
            return None
        return proposal

    def apply_split(self, to_global: list[str]) -> dict:
        """Move the named sections from the workspace persona into the
        global one. Call only after the user confirmed the selection
        (multiple-choice, per propose_split). Machine-owned sections are
        refused. Idempotent per section: already-moved names are skipped."""
        machine = {m.lstrip("# ").strip().lower() for m in MACHINE_MARKERS}
        for name in to_global:
            if name.strip().lower() in machine:
                raise ValueError(f"machine-owned section can't move: {name!r}")
        text = self.read() or ""
        sections = _sections(text)
        by_name = {h.strip().lower(): (h, b) for h, b in sections}

        self.init_global()
        global_text = self.read_global() or _GLOBAL_TEMPLATE
        moved, missing = [], []
        for name in to_global:
            got = by_name.get(name.strip().lower())
            if got is None:
                missing.append(name)
                continue
            heading, body = got
            global_text = _upsert_section(global_text, heading, body)
            text = _remove_section(text, heading)
            moved.append(heading)

        if moved:
            self.global_path.write_text(global_text, encoding="utf-8")
            self.path.write_text(text, encoding="utf-8")
        return {"moved": moved, "missing": missing,
                "global_path": str(self.global_path),
                "workspace_path": str(self.path)}

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


def _sections(text: str) -> list[tuple[str, str]]:
    """``[(heading, body)]`` for every ``## `` section in a persona file."""
    out: list[tuple[str, str]] = []
    lines = text.splitlines()
    current: Optional[str] = None
    body: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if current is not None:
                out.append((current, "\n".join(body)))
            current, body = line[3:].strip(), []
        elif current is not None:
            body.append(line)
    if current is not None:
        out.append((current, "\n".join(body)))
    return out


def _remove_section(text: str, heading: str) -> str:
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines)
                  if l.strip() == f"## {heading}"), None)
    if start is None:
        return text
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("## ")), len(lines))
    kept = lines[:start] + lines[end:]
    return "\n".join(kept).rstrip("\n") + "\n"


def _upsert_section(text: str, heading: str, body: str) -> str:
    """Replace the section's body if the heading exists (template comment
    placeholders count as replaceable); append the section otherwise."""
    lines = text.splitlines()
    body_lines = ["", *body.strip("\n").splitlines(), ""]
    start = next((i for i, l in enumerate(lines)
                  if l.strip() == f"## {heading}"), None)
    if start is not None:
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].startswith("## ")), len(lines))
        existing = [l for l in lines[start + 1:end]
                    if l.strip() and not l.strip().startswith("<!--")]
        if existing:
            # Real content on both sides: keep both rather than clobber.
            body_lines = [*existing, "", *body.strip("\n").splitlines(), ""]
        lines = lines[:start + 1] + body_lines + lines[end:]
    else:
        lines += [f"## {heading}", *body_lines]
    return "\n".join(lines).rstrip("\n") + "\n"


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
