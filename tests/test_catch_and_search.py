from openpod.catch import catch
from openpod.search import search, reindex
from openpod.exports import export_timestamps


def _catch_sample(workspace, vtt_file):
    return catch(
        "https://example.com/ep1",
        workspace=workspace,
        kind="podcast",
        transcript_path=str(vtt_file),
    )


def test_catch_writes_artifacts(workspace, vtt_file):
    result = _catch_sample(workspace, vtt_file)
    entry = result.entry
    assert entry.exists()
    assert entry.transcript_path.exists()
    assert entry.briefing_path.exists()
    assert entry.ideas_path.exists()
    assert len(result.transcript) == 5
    assert result.ideas  # something salient was extracted
    # briefing scaffold has the agent-facing sections
    briefing = entry.briefing_path.read_text()
    assert "## Triage" in briefing
    assert "## Navigate" in briefing


def test_search_finds_caught_content(workspace, vtt_file):
    _catch_sample(workspace, vtt_file)
    hits = search("raft consensus", workspace=workspace)
    assert hits
    assert any("raft" in h.text.lower() or "consensus" in h.text.lower() for h in hits)
    top = hits[0]
    assert top.entry_id
    assert top.start >= 0


def test_search_semantic_fallback(workspace, vtt_file):
    _catch_sample(workspace, vtt_file)
    # a word not present verbatim; semantic scan should still return something
    hits = search("nonexistentterm", workspace=workspace, semantic=True)
    assert isinstance(hits, list)  # never raises


def test_reindex_roundtrip(workspace, vtt_file):
    _catch_sample(workspace, vtt_file)
    n = reindex(workspace)
    assert n == 5


def test_export_timestamps(workspace, vtt_file):
    result = _catch_sample(workspace, vtt_file)
    md = export_timestamps(result.entry_id, workspace=workspace, fmt="markdown")
    assert md.startswith("# Timestamps")
    js = export_timestamps(result.entry_id, workspace=workspace, fmt="json")
    assert '"segments"' in js
