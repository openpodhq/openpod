import json

from openpod.catch import catch
from openpod.cli import main


def _entry(workspace, vtt_file, link="https://example.com/ep1"):
    return catch(link, workspace=workspace, kind="podcast",
                 transcript_path=str(vtt_file)).entry


BODY = ("## What mattered\n\n"
        "Raft vs Paxos tradeoffs — the user is choosing a consensus layer "
        "([2:15](https://example.com/ep1#t=135)).\n\n"
        "Skipped: the vector-DB segment (not relevant to this user).")


def test_write_and_read_roundtrip(workspace, vtt_file):
    entry = _entry(workspace, vtt_file)
    path = entry.write_summary(BODY)
    assert path == entry.summary_path and path.exists()
    # body comes back clean; raw carries product-owned frontmatter
    assert entry.read_summary(body_only=True).strip() == BODY
    raw = entry.read_summary()
    assert raw.startswith("---")
    assert f"entry_id: {entry.entry_id}" in raw
    assert "updated_at: " in raw
    assert "revisions: 1" in raw


def test_frontmatter_is_product_owned_and_never_duplicated(workspace, vtt_file):
    entry = _entry(workspace, vtt_file)
    # An agent that pastes its own frontmatter doesn't get to double it.
    entry.write_summary("---\nauthor: some-agent\n---\n" + BODY)
    raw = entry.read_summary()
    assert raw.count("---\n") == 2                # one product block only
    assert "some-agent" not in raw


def test_append_keeps_prior_sessions_and_bumps_revisions(workspace, vtt_file):
    entry = _entry(workspace, vtt_file)
    entry.write_summary(BODY)
    entry.write_summary("## Session 2026-07-09\n\nDecided to prototype Raft.",
                        append=True)
    body = entry.read_summary(body_only=True)
    assert "What mattered" in body                 # first session survived
    assert "prototype Raft" in body
    assert "revisions: 2" in entry.read_summary()


def test_replace_rewrites_distillation(workspace, vtt_file):
    entry = _entry(workspace, vtt_file)
    entry.write_summary(BODY)
    entry.write_summary("## Revised\n\nOne tight paragraph.")
    body = entry.read_summary(body_only=True)
    assert "What mattered" not in body
    assert "revisions: 2" in entry.read_summary()


def test_summary_carries_episode_key_for_future_sync(workspace, vtt_file):
    entry = _entry(workspace, vtt_file)
    if entry.read_meta().get("episode_key"):
        entry.write_summary(BODY)
        assert "episode_key: " in entry.read_summary()


def test_doctor_accepts_summary_md(workspace, vtt_file):
    from openpod.doctor import check

    entry = _entry(workspace, vtt_file)
    entry.write_summary(BODY)
    assert check(workspace)["library"]["foreign"] == []


def test_cli_summary_write_read_list(workspace, vtt_file, tmp_path, capsys):
    entry = _entry(workspace, vtt_file)
    src = tmp_path / "s.md"
    src.write_text(BODY, encoding="utf-8")
    home = str(workspace.root)

    assert main(["--home", home, "summary", entry.entry_id,
                 "--from", str(src)]) == 0
    capsys.readouterr()
    assert main(["--home", home, "summary", entry.entry_id]) == 0
    out = capsys.readouterr().out
    assert "What mattered" in out and "---" not in out
    assert main(["--home", home, "summary"]) == 0   # recall listing
    assert entry.entry_id in capsys.readouterr().out


def test_cli_summary_missing(workspace, vtt_file, capsys):
    entry = _entry(workspace, vtt_file)
    assert main(["--home", str(workspace.root), "summary", entry.entry_id]) == 1
    assert "no summary" in capsys.readouterr().out
