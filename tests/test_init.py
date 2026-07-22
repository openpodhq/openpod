"""`openpod init` — the workspace front door writes the agent contract."""

from openpod.agents_doc import AGENTS_BASENAME, agents_md_text
from openpod.cli import main


def test_init_creates_workspace_and_contract(tmp_path, capsys):
    assert main(["--home", str(tmp_path), "init"]) == 0
    assert (tmp_path / ".openpod").is_dir()
    doc = (tmp_path / AGENTS_BASENAME).read_text(encoding="utf-8")
    assert doc == agents_md_text()
    # the load-bearing clauses: ask-don't-answer, product-owned
    # presentation, clean masters, no names from memory
    lower = doc.lower()
    assert "the user decides" in lower
    assert "never answer them on the user's behalf" in lower
    assert "openpod skills" in doc
    assert ".openpod/library/" in doc
    assert "name from memory" in lower
    out = capsys.readouterr().out
    assert AGENTS_BASENAME in out


def test_init_never_clobbers_an_existing_contract(tmp_path, capsys):
    target = tmp_path / AGENTS_BASENAME
    target.write_text("house rules\n", encoding="utf-8")
    assert main(["--home", str(tmp_path), "init"]) == 1
    assert target.read_text(encoding="utf-8") == "house rules\n"
    out = capsys.readouterr().out
    assert "--print" in out and "--force" in out
    # --force is the explicit overwrite path
    assert main(["--home", str(tmp_path), "init", "--force"]) == 0
    assert target.read_text(encoding="utf-8") == agents_md_text()


def test_init_print_writes_nothing(tmp_path, capsys):
    assert main(["--home", str(tmp_path), "init", "--print"]) == 0
    assert capsys.readouterr().out.strip() == agents_md_text().strip()
    assert not (tmp_path / AGENTS_BASENAME).exists()
    assert not (tmp_path / ".openpod").exists()
