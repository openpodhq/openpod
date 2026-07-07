"""Imports (§2.5), the skill catalog (§3), notes, and the persona authorship
boundary (§2.2) — the guarantees the Library UI spec calls load-bearing."""

import json
import textwrap

from openpod.catch import catch
from openpod.cli import main
from openpod.follows import Follows
from openpod.imports import import_opml, parse_opml
from openpod.library import Library
from openpod.persona import DERIVED_MARKER, IMPORTED_MARKER, Persona
from openpod.skills import get_skill, list_skills

SAMPLE_OPML = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="UTF-8"?>
    <opml version="1.0">
      <head><title>Overcast Subscriptions</title></head>
      <body>
        <outline text="podcasts">
          <outline text="Latent Space" type="rss"
                   xmlUrl="https://example.com/latent-space.xml"/>
          <outline text="Lex Fridman Podcast" type="rss"
                   xmlUrl="https://example.com/lex.xml"/>
          <outline title="Acquired" type="rss"
                   xmlUrl="https://example.com/acquired.xml"/>
        </outline>
      </body>
    </opml>
    """
)

CATCHY_HUMAN_SECTIONS = textwrap.dedent(
    """\
    # Persona

    ## Role
    PM shipping an agents feature.

    ## Interests (amplify)
    - orchestration frameworks

    ## Not interested (filter)
    - crypto
    """
)


def _opml_file(tmp_path, name="overcast.opml"):
    p = tmp_path / name
    p.write_text(SAMPLE_OPML, encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# OPML parsing + import
# --------------------------------------------------------------------------- #


def test_parse_opml_handles_nesting_and_attr_spellings():
    feeds = parse_opml(SAMPLE_OPML)
    assert ("Latent Space", "https://example.com/latent-space.xml") in feeds
    assert ("Acquired", "https://example.com/acquired.xml") in feeds  # title=
    assert len(feeds) == 3  # container outline without xmlUrl is skipped


def test_import_opml_stages_merges_and_tags_provenance(workspace, tmp_path):
    result = import_opml(str(_opml_file(tmp_path)), workspace=workspace)

    # raw export staged verbatim, inspectable and deletable
    assert result.staged_path.is_file()
    assert result.staged_path.parent == workspace.imports_dir
    assert result.staged_path.read_text(encoding="utf-8") == SAMPLE_OPML

    # subscriptions merged with source provenance
    follows = Follows(workspace).list()
    assert len(follows) == 3 and len(result.added) == 3
    assert all(f.source == "opml:overcast" for f in follows)

    # persona gained the machine-owned imported block, with the marker contract
    text = Persona(workspace).read()
    assert IMPORTED_MARKER in text
    assert "Latent Space" in text
    assert text.index(IMPORTED_MARKER) < text.index(DERIVED_MARKER)


def test_import_is_a_rerunnable_snapshot_deduped_by_feed_url(workspace, tmp_path):
    opml = _opml_file(tmp_path)
    Follows(workspace).add("https://example.com/lex.xml", title="Lex")  # by hand
    first = import_opml(str(opml), workspace=workspace)
    assert len(first.added) == 2 and first.skipped == 1
    second = import_opml(str(opml), workspace=workspace)
    assert len(second.added) == 0 and second.skipped == 3
    assert len(Follows(workspace).list()) == 3
    # the hand-added follow keeps no provenance tag
    lex = next(f for f in Follows(workspace).list()
               if f.url.endswith("lex.xml"))
    assert lex.source is None


# --------------------------------------------------------------------------- #
# The authorship boundary — provably non-destructive above the markers
# --------------------------------------------------------------------------- #


def test_derive_and_import_never_touch_human_sections(workspace, tmp_path,
                                                      vtt_file):
    persona = Persona(workspace)
    persona.path.parent.mkdir(parents=True, exist_ok=True)
    persona.path.write_text(CATCHY_HUMAN_SECTIONS, encoding="utf-8")

    catch("https://example.com/ep1", workspace=workspace, kind="podcast",
          transcript_path=str(vtt_file))
    persona.derive()
    import_opml(str(_opml_file(tmp_path)), workspace=workspace)
    persona.derive()  # again, after import — both machine blocks must survive

    text = persona.read()
    # every human byte is preserved verbatim
    assert text.startswith(CATCHY_HUMAN_SECTIONS.rstrip("\n"))
    # both machine blocks present, imported above derived
    assert text.count(IMPORTED_MARKER) == 1
    assert text.count(DERIVED_MARKER) == 1
    assert text.index(IMPORTED_MARKER) < text.index(DERIVED_MARKER)
    assert "Episodes caught" in text and "Latent Space" in text


def test_derive_is_idempotent(workspace, vtt_file):
    persona = Persona(workspace)
    persona.init()
    catch("https://example.com/ep1", workspace=workspace, kind="podcast",
          transcript_path=str(vtt_file))
    persona.derive()
    once = persona.read()
    persona.derive()
    assert persona.read() == once


# --------------------------------------------------------------------------- #
# Notes — user-authored, agent-append-on-request
# --------------------------------------------------------------------------- #


def test_note_cli_appends_and_reports_path(workspace, vtt_file, capsys):
    r = catch("https://example.com/ep1", workspace=workspace, kind="podcast",
              transcript_path=str(vtt_file))
    assert main(["--home", str(workspace.root), "note", r.entry_id,
                 "the Raft segment matters for our design doc"]) == 0
    out = capsys.readouterr().out
    entry = Library(workspace).get(r.entry_id)
    assert str(entry.notes_path) in out
    assert "Raft segment" in entry.notes_path.read_text(encoding="utf-8")


def test_note_unknown_entry_names_the_fix(workspace, capsys):
    assert main(["--home", str(workspace.root), "note", "no/such", "x"]) == 1
    assert "openpod list" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Skills — the packaged features
# --------------------------------------------------------------------------- #

CATALOG = {
    "catch-me-up", "set-up-my-persona", "bring-in-my-world",
    "sharpen-my-persona", "find-the-moment", "cut-the-clip", "whats-new",
    "chapter-it", "follow-this",
}


def test_skill_catalog_is_complete_and_versioned():
    skills = {s.slug: s for s in list_skills()}
    assert set(skills) == CATALOG
    for s in skills.values():
        assert s.name and s.description and s.version and s.body
        assert s.primitives, f"{s.slug} declares no primitives"


def test_flagship_skill_states_the_contract():
    s = get_skill("catch-me-up")
    assert "persona" in s.body.lower()
    assert "deep-link" in s.body.lower()
    assert "briefing.md" in s.body


def test_skills_cli_lists_catalog(workspace, capsys):
    assert main(["--home", str(workspace.root), "skills", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert {s["slug"] for s in listed} == CATALOG


# --------------------------------------------------------------------------- #
# CLI surface — one schema per capability, paths on every mutation
# --------------------------------------------------------------------------- #


def test_import_cli_reports_every_path_written(workspace, tmp_path, capsys):
    opml = _opml_file(tmp_path)
    assert main(["--home", str(workspace.root), "import", str(opml)]) == 0
    out = capsys.readouterr().out
    assert str(workspace.imports_dir) in out
    assert str(workspace.follows_file) in out
    assert str(workspace.persona_file) in out


def test_catch_json_matches_mcp_schema_and_nudges(workspace, vtt_file, capsys):
    assert main(["--home", str(workspace.root), "catch",
                 "https://example.com/ep1", "--kind", "podcast",
                 "--transcript", str(vtt_file), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {"entry_id", "title", "show", "source_kind", "transcript_cues",
            "transcript_source", "artifacts_dir", "ideas",
            "toc"} <= set(payload)
    assert "Set Up My Persona" in payload["next_step"]  # empty library nudge


def test_follows_json_carries_provenance(workspace, tmp_path, capsys):
    import_opml(str(_opml_file(tmp_path)), workspace=workspace)
    assert main(["--home", str(workspace.root), "follows", "--json"]) == 0
    follows = json.loads(capsys.readouterr().out)
    assert all(f["source"] == "opml:overcast" for f in follows)
