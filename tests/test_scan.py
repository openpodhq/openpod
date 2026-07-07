"""Workspace evidence scan — the guess-and-confirm interview's raw material."""

import json

from openpod.cli import main
from openpod.follows import Follows
from openpod.persona import INTERVIEW_QUESTIONS
from openpod.scan import scan_workspace


def _seed_tree(root):
    (root / "KnowTree" / "Content").mkdir(parents=True)
    (root / "OpenPod" / "Docs").mkdir(parents=True)
    (root / "OpenPod" / "Docs" / "OSS_Marketing_Strategy.md").write_text(
        "# OpenPod OSS Marketing Strategy\n\ndetails…", encoding="utf-8")
    (root / "CLAUDE.md").write_text(
        "# Project notes\n\n## Naval clipper pipeline\n## Carousel engine\n",
        encoding="utf-8")
    (root / "node_modules").mkdir()          # noise: must be ignored
    (root / "node_modules" / "junk.md").write_text("# junk", encoding="utf-8")


def test_scan_surfaces_projects_docs_and_claude_headings(workspace):
    _seed_tree(workspace.root)
    evidence = scan_workspace(workspace)

    names = [p["name"] for p in evidence["projects"]]
    assert "KnowTree" in names and "OpenPod" in names
    assert "node_modules" not in names

    titles = [d["title"] for d in evidence["docs"]]
    assert "OpenPod OSS Marketing Strategy" in titles
    assert "junk" not in titles

    claude = evidence["claude_files"]
    assert claude and "Naval clipper pipeline" in claude[0]["headings"]
    assert evidence["hint"]  # tells the agent to guess, not ask


def test_scan_includes_library_and_follows_signal(workspace, vtt_file):
    from openpod.catch import catch

    catch("https://example.com/ep1", workspace=workspace, kind="podcast",
          transcript_path=str(vtt_file))
    Follows(workspace).add("https://example.com/feed.xml", title="Test Pod",
                           source="opml:overcast")
    evidence = scan_workspace(workspace)
    assert evidence["library"]["episodes"] == 1
    assert "raft" in evidence["library"]["themes"]
    assert evidence["follows"][0]["source"] == "opml:overcast"


def test_scan_extra_roots_are_opt_in(workspace, tmp_path):
    other = tmp_path / "elsewhere"
    (other / "SecretProject").mkdir(parents=True)
    evidence = scan_workspace(workspace)
    assert all(p["name"] != "SecretProject" for p in evidence["projects"])
    evidence = scan_workspace(workspace, extra_roots=[str(other)])
    assert any(p["name"] == "SecretProject" for p in evidence["projects"])


def test_persona_scan_cli_is_read_only_json(workspace, capsys):
    _seed_tree(workspace.root)
    before = sorted(str(p) for p in workspace.dot.rglob("*"))
    assert main(["--home", str(workspace.root), "persona", "scan"]) == 0
    evidence = json.loads(capsys.readouterr().out)
    assert evidence["projects"]
    after = sorted(str(p) for p in workspace.dot.rglob("*"))
    assert before == after  # reads never write


def test_interview_is_guess_and_confirm_without_trust_question():
    text = " ".join(INTERVIEW_QUESTIONS).lower()
    assert "trust" not in text  # follows/OPML answer that better
    assert "multi-select" in text
    assert len(INTERVIEW_QUESTIONS) == 5
