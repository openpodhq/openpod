import textwrap

import pytest

from openpod.persona import DERIVED_MARKER, Persona

LEGACY = textwrap.dedent("""\
    # Persona

    ## Role
    Independent founder & developer; building products and a personal brand.

    ## Current projects
    - KnowTree content engine

    ## Interests (amplify)
    - retention loops

    ## What I want from long-form
    Decisions and named tools.

    ## Custom section the user invented
    Something personal.

    ## Derived from my library
    - **Episodes caught:** 3
    """)


def _legacy(workspace):
    p = Persona(workspace)
    workspace.dot.mkdir(parents=True, exist_ok=True)
    p.path.write_text(LEGACY, encoding="utf-8")
    return p


# -- layers ------------------------------------------------------------------- #

def test_global_layer_init_and_read(workspace):
    p = Persona(workspace)
    assert not p.global_exists()
    path = p.init_global()
    assert p.global_exists() and "Persona — global" in p.read_global()
    assert p.init_global() == path                # idempotent
    layers = p.read_layers()
    assert layers["global"]["exists"] and not layers["workspace"]["exists"]


def test_global_home_is_isolated_from_workspace(workspace):
    p = Persona(workspace)
    p.init_global()
    assert not str(p.global_path).startswith(str(workspace.root))


# -- split proposal ------------------------------------------------------------ #

def test_propose_split_classifies_sections(workspace):
    p = _legacy(workspace)
    proposal = p.propose_split()
    proposed = {i["section"] for i in proposal["proposed_global"]}
    local = {i["section"] for i in proposal["stays_local"]}
    unclassified = {i["section"] for i in proposal["unclassified"]}
    assert proposed == {"Role", "What I want from long-form"}
    assert "Current projects" in local and "Interests (amplify)" in local
    assert "Derived from my library" in local     # machine block never offered
    assert unclassified == {"Custom section the user invented"}


def test_propose_split_none_when_global_exists(workspace):
    p = _legacy(workspace)
    p.init_global()
    assert p.propose_split() is None


def test_propose_split_none_without_workspace_persona(workspace):
    assert Persona(workspace).propose_split() is None


# -- apply split ---------------------------------------------------------------- #

def test_apply_split_moves_sections(workspace):
    p = _legacy(workspace)
    result = p.apply_split(["Role", "What I want from long-form"])
    assert result["moved"] == ["Role", "What I want from long-form"]
    g = p.read_global()
    w = p.read()
    assert "Independent founder" in g
    assert "Decisions and named tools." in g
    assert "Independent founder" not in w         # moved, not copied
    assert "## Current projects" in w             # local stayed
    assert DERIVED_MARKER in w                    # machine block untouched
    assert "Episodes caught" in w


def test_apply_split_refuses_machine_sections(workspace):
    p = _legacy(workspace)
    with pytest.raises(ValueError):
        p.apply_split(["Derived from my library"])


def test_apply_split_reports_missing(workspace):
    p = _legacy(workspace)
    result = p.apply_split(["Role", "Nonexistent"])
    assert result["moved"] == ["Role"]
    assert result["missing"] == ["Nonexistent"]


def test_apply_split_then_no_more_proposal(workspace):
    p = _legacy(workspace)
    p.apply_split(["Role"])
    assert p.propose_split() is None              # global exists now


def test_derive_still_only_touches_derived_block(workspace):
    p = _legacy(workspace)
    p.apply_split(["Role"])
    p.derive()
    w = p.read()
    assert "## Current projects" in w
    assert "- KnowTree content engine" in w
