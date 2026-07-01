import openpod.transcript as tx
from openpod.clip import snap_to_cues
from openpod.cli import main


def test_snap_to_cues_expands_to_boundaries(vtt_file):
    t = tx.load_file(vtt_file)
    # request a span inside cue 2 (5–11) to inside cue 3 (11–18)
    start, end = snap_to_cues(t, 7.0, 13.0)
    assert start == 5.0        # snapped down to cue-2 start
    assert end >= 18.0         # snapped up to cue-3 end


def test_cli_catch_search_list(workspace, vtt_file, capsys, monkeypatch):
    monkeypatch.setenv("OPENPOD_HOME", str(workspace.root))

    assert main(["catch", "https://example.com/ep1", "--kind", "podcast",
                 "--transcript", str(vtt_file)]) == 0
    out = capsys.readouterr().out
    assert "caught:" in out

    assert main(["search", "consensus"]) == 0
    out = capsys.readouterr().out
    assert "consensus" in out.lower()

    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "/" in out  # entry id show/episode


def test_cli_version(capsys):
    assert main(["version"]) == 0
    assert "openpod" in capsys.readouterr().out


def test_cli_unknown_entry_errors(workspace, monkeypatch, capsys):
    monkeypatch.setenv("OPENPOD_HOME", str(workspace.root))
    rc = main(["export-timestamps", "no/such"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err
