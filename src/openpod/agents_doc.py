"""The workspace agent contract — ``AGENTS.md``.

``openpod init`` writes this file at the workspace root so any agent that
opens the folder (Claude Code, Codex, Cursor, …) reads the interaction
contract before its first tool call: who decides what, presentation goes
through the product, where the playbooks are. It exists because the vacuum
gets filled — an agent in an undocumented workspace once answered the
user's one-time setup questions itself and hand-rolled a second caption
renderer next to the product's own.

The text ships inside the package (``workspace_agents.md``) rather than
inline so it stays reviewable as prose. It is deliberately NOT named
AGENTS.md in-package, so a repo-browsing agent never mistakes the template
for instructions about this source tree.
"""

from __future__ import annotations

from importlib import resources

AGENTS_BASENAME = "AGENTS.md"
_TEMPLATE = "workspace_agents.md"


def agents_md_text() -> str:
    """The contract, exactly as ``openpod init`` writes it."""
    return (resources.files("openpod") / _TEMPLATE).read_text(encoding="utf-8")
