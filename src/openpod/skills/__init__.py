"""Skills — the unit of feature.

Primitives are the API; **skills are the product**. A skill is a named,
versioned bundle of instructions that an agent follows: it reads the persona,
composes the primitives (catch / search / get_briefing / clip /
export_timestamps / follow / digest / import), writes cited artifacts back to
the tree, and reports the paths. Each bundle is a plain ``SKILL.md`` with YAML
frontmatter, shipped inside the package under ``openpod/skills/`` so the MCP
server can expose them (as prompts) alongside the tools — install OpenPod and
the features show up. A skill carries no model and no secrets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Optional

SKILLS_PACKAGE = "openpod.skills"


@dataclass
class Skill:
    """One packaged feature: frontmatter metadata + the agent instructions."""

    slug: str
    name: str
    description: str
    version: str
    path: Path
    primitives: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    body: str = ""

    def to_dict(self, *, include_body: bool = False) -> dict:
        d = {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "primitives": self.primitives,
            "artifacts": self.artifacts,
            "path": str(self.path),
        }
        if include_body:
            d["body"] = self.body
        return d


def skills_dir() -> Path:
    """The packaged skills directory on disk."""
    return Path(str(resources.files(SKILLS_PACKAGE)))


def list_skills() -> list[Skill]:
    """All packaged skills, sorted by slug."""
    base = skills_dir()
    out = []
    for md in sorted(base.glob("*/SKILL.md")):
        out.append(_load(md))
    return out


def get_skill(slug: str) -> Optional[Skill]:
    md = skills_dir() / slug / "SKILL.md"
    return _load(md) if md.is_file() else None


def _load(md: Path) -> Skill:
    import yaml

    text = md.read_text(encoding="utf-8")
    meta: dict = {}
    body = text
    if text.startswith("---\n"):
        _, fm, body = text.split("---\n", 2)
        meta = yaml.safe_load(fm) or {}
    slug = md.parent.name
    return Skill(
        slug=slug,
        name=meta.get("name", slug),
        description=meta.get("description", ""),
        version=str(meta.get("version", "0")),
        primitives=list(meta.get("primitives", [])),
        artifacts=list(meta.get("artifacts", [])),
        path=md,
        body=body.strip(),
    )
