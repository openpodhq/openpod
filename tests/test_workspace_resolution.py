"""Workspace resolution and the $OPENPOD_HOME ↔ cwd mismatch guard.

The field incident these tests encode: OPENPOD_HOME in a shell profile
pointed at the production library; agents launched inside fresh test
workspaces (cwd had its own .openpod/) silently wrote catches into
production. Reads must warn, writes must refuse, and an explicit --home
must always win without commentary.
"""

import json

import pytest

from openpod.cli import main
from openpod.config import DOTDIR, Workspace


def _make_workspace(path):
    (path / DOTDIR).mkdir(parents=True)
    return path


@pytest.fixture
def env_ws(tmp_path_factory, monkeypatch):
    """A workspace that $OPENPOD_HOME points at (the "production" library)."""
    root = _make_workspace(tmp_path_factory.mktemp("env-ws"))
    monkeypatch.setenv("OPENPOD_HOME", str(root))
    return root


@pytest.fixture
def cwd_ws(tmp_path_factory, monkeypatch):
    """A different workspace the process sits inside (the "test" library)."""
    root = _make_workspace(tmp_path_factory.mktemp("cwd-ws"))
    monkeypatch.chdir(root)
    return root


# -- resolution ------------------------------------------------------------- #


def test_explicit_root_beats_env_and_never_conflicts(env_ws, cwd_ws, tmp_path):
    ws = Workspace(tmp_path)
    assert ws.root == tmp_path.resolve()
    assert ws.origin == "arg"
    assert ws.cwd_conflict is None


def test_env_wins_but_the_conflict_is_recorded(env_ws, cwd_ws):
    ws = Workspace()
    assert ws.root == env_ws.resolve()
    assert ws.origin == "env"
    assert ws.cwd_conflict == cwd_ws.resolve()


def test_no_conflict_when_cwd_is_inside_the_env_workspace(env_ws, monkeypatch):
    sub = env_ws / "notes" / "deep"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    ws = Workspace()
    assert ws.cwd_conflict is None


def test_no_conflict_when_cwd_has_no_workspace(env_ws, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert Workspace().cwd_conflict is None


def test_walk_up_still_wins_without_env(cwd_ws, monkeypatch):
    monkeypatch.delenv("OPENPOD_HOME", raising=False)
    sub = cwd_ws / "deep"
    sub.mkdir()
    monkeypatch.chdir(sub)
    ws = Workspace()
    assert ws.root == cwd_ws.resolve()
    assert ws.origin == "cwd"
    assert ws.cwd_conflict is None


def test_home_global_layer_is_not_a_rival_workspace(env_ws, tmp_path_factory,
                                                    monkeypatch):
    # ~/.openpod is the global persona layer; a walk-up landing on the home
    # directory must not trip the guard from every dir under $HOME.
    fake_home = tmp_path_factory.mktemp("fake-home")
    (fake_home / DOTDIR).mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    inside = fake_home / "some" / "project"
    inside.mkdir(parents=True)
    monkeypatch.chdir(inside)
    assert Workspace().cwd_conflict is None


# -- CLI enforcement -------------------------------------------------------- #


def test_read_command_warns_on_stderr_and_proceeds(env_ws, cwd_ws, capsys):
    assert main(["list"]) == 0
    err = capsys.readouterr().err
    assert "OPENPOD_HOME" in err
    assert str(cwd_ws.resolve()) in err


def test_read_json_stdout_stays_parseable(env_ws, cwd_ws, capsys):
    assert main(["list", "--json"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out) == []


def test_write_command_refuses_with_structured_error(env_ws, cwd_ws, capsys):
    rc = main(["settings", "clip.captions", "soft"])
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "workspace_mismatch"
    assert payload["env_root"] == str(env_ws.resolve())
    assert payload["cwd_root"] == str(cwd_ws.resolve())
    assert "--home" in payload["next_step"] or any(
        "--home" in o["description"] for o in payload["options"])
    # nothing landed in either library
    assert not (env_ws / DOTDIR / "settings.yaml").exists()
    assert not (cwd_ws / DOTDIR / "settings.yaml").exists()


def test_init_refuses_in_the_mismatch_state(env_ws, cwd_ws, capsys):
    rc = main(["init"])
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["error"] == "workspace_mismatch"


def test_settings_read_form_only_warns(env_ws, cwd_ws, capsys):
    assert main(["settings", "clip.captions"]) == 0
    captured = capsys.readouterr()
    assert "OPENPOD_HOME" in captured.err
    assert json.loads(captured.out) == "off"


def test_explicit_home_disambiguates_the_write(env_ws, cwd_ws, capsys):
    rc = main(["--home", str(cwd_ws), "settings", "clip.captions", "soft"])
    assert rc == 0
    assert (cwd_ws / DOTDIR / "settings.yaml").exists()
    assert not (env_ws / DOTDIR / "settings.yaml").exists()
    assert "OPENPOD_HOME" not in capsys.readouterr().err


def test_explicit_home_may_also_choose_the_env_workspace(env_ws, cwd_ws):
    rc = main(["--home", str(env_ws), "settings", "clip.captions", "soft"])
    assert rc == 0
    assert (env_ws / DOTDIR / "settings.yaml").exists()


def test_init_in_a_fresh_dir_requires_home_when_env_is_set(
        env_ws, tmp_path, monkeypatch, capsys):
    # The incident's blind spot: no rival .openpod in cwd, so the mismatch
    # guard is silent — but the env var would redirect the new workspace.
    monkeypatch.chdir(tmp_path)
    rc = main(["init"])
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "init_needs_home"
    assert payload["env_root"] == str(env_ws.resolve())
    assert not (env_ws / "AGENTS.md").exists()
    assert not (tmp_path / DOTDIR).exists()


def test_init_with_explicit_home_proceeds(env_ws, tmp_path, monkeypatch,
                                          capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["--home", str(tmp_path), "init"]) == 0
    assert (tmp_path / DOTDIR).is_dir()
    assert (tmp_path / "AGENTS.md").exists()
    assert not (env_ws / "AGENTS.md").exists()


def test_init_inside_the_env_workspace_needs_no_home(env_ws, monkeypatch,
                                                     capsys):
    # cwd already belongs to the $OPENPOD_HOME workspace — nothing to
    # disambiguate, and the output names the env var that picked the target.
    sub = env_ws / "notes"
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert main(["init"]) == 0
    assert (env_ws / "AGENTS.md").exists()
    assert "OPENPOD_HOME" in capsys.readouterr().out


def test_init_print_is_exempt(env_ws, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--print"]) == 0
    assert "error" not in capsys.readouterr().err


def test_no_warning_without_a_conflict(env_ws, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["list"]) == 0
    assert "NOTE" not in capsys.readouterr().err
