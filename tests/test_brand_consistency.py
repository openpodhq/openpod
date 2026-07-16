"""Guardrail tests for brand consistency across the package (WO-5).

theme.py is the single place colors and glyphs are allowed to be defined —
these tests fail the build if that boundary leaks, if forbidden vocabulary
creeps into user-facing copy, or if the README's above-the-fold install
line drifts.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "src" / "openpod"

# The single canonical brand headline. If the decision changes, change it HERE
# (and in brand-tokens.json, which this test pins to it) — never by hand-editing
# individual surfaces. This assertion is the guard that would have caught the
# five-vs-ten headline drift.
CANONICAL_TAGLINE = "Pull the ten minutes that matter — to you."

# Retired headlines/outcome lines: must never reappear in shipped copy. The
# product outcome is "ten minutes" everywhere now, so any stray "five minutes"
# outcome line is drift.
SUPERSEDED_HEADLINES = (
    "five minutes that matter",
    "five minutes, not the three hours",
)

PALETTE_HEX = (
    "#0E0E0E", "#0e0e0e",  # ink
    "#FCFCFB", "#fcfcfb",  # paper
    "#0969DA", "#0969da",  # blue · light
    "#58A6FF", "#58a6ff",  # blue · dark
)


def _py_files_outside_theme():
    for path in PACKAGE_SRC.rglob("*.py"):
        if path.name == "theme.py":
            continue
        yield path


def test_no_palette_hex_literal_outside_theme():
    offenders = [
        f"{path}: {hex_code}"
        for path in _py_files_outside_theme()
        for hex_code in PALETTE_HEX
        if hex_code in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        "palette hex literals must live only in theme.py / brand-tokens.json "
        "(hand-copying a hex value elsewhere breaks the single source of "
        f"truth):\n" + "\n".join(offenders)
    )


def test_no_raw_ansi_truecolor_escape_outside_theme():
    offenders = [
        str(path)
        for path in _py_files_outside_theme()
        if "38;2" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"raw ANSI truecolor escapes leaked outside theme.py: {offenders}"
    )


# "download your" / "downloader" / "grab" / "rip" read as re-hosting or
# take-it-with-you language — OpenPod navigates, it doesn't take. The local
# ASR fallback sentence is the one allowed exception (see README).
FORBIDDEN_PHRASES = (r"download your", r"downloader", r"\bgrab\b", r"\brip\b")
ALLOWLIST_SUBSTRINGS = ("audio is downloaded and transcribed",)


def _forbidden_vocab_hits(path: Path):
    hits = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if any(allowed in line for allowed in ALLOWLIST_SUBSTRINGS):
            continue
        for phrase in FORBIDDEN_PHRASES:
            if re.search(phrase, line, re.IGNORECASE):
                hits.append((path, lineno, phrase, line.strip()))
    return hits


def test_readme_has_no_forbidden_vocabulary():
    hits = _forbidden_vocab_hits(REPO_ROOT / "README.md")
    assert not hits, f"forbidden vocabulary in README.md: {hits}"


def test_cli_strings_have_no_forbidden_vocabulary():
    hits = [hit for path in PACKAGE_SRC.rglob("*.py") for hit in _forbidden_vocab_hits(path)]
    assert not hits, f"forbidden vocabulary in src/: {hits}"


def test_brand_tokens_tagline_is_canonical():
    import json

    tokens = json.loads(
        (PACKAGE_SRC / "brand" / "brand-tokens.json").read_text(encoding="utf-8")
    )
    assert tokens.get("tagline") == CANONICAL_TAGLINE, (
        "brand-tokens.json tagline drifted from the canonical headline: "
        f"{tokens.get('tagline')!r} != {CANONICAL_TAGLINE!r}"
    )


def test_no_superseded_headline_in_shipped_copy():
    surfaces = [REPO_ROOT / "README.md"]
    surfaces += list(PACKAGE_SRC.rglob("*.py"))
    surfaces += [p for p in (PACKAGE_SRC / "brand").glob("*") if p.is_file()]
    offenders = []
    for path in surfaces:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError):
            continue
        for retired in SUPERSEDED_HEADLINES:
            if retired.lower() in text.lower():
                offenders.append(f"{path}: {retired!r}")
    assert not offenders, (
        "a retired headline is still present in shipped copy (update it to the "
        f"canonical line):\n" + "\n".join(offenders)
    )


def test_readme_has_exactly_one_pip_install_openpod_above_the_fold():
    lines = (REPO_ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    above_the_fold = "\n".join(lines[:30])
    assert above_the_fold.count("pip install openpod") == 1


def test_briefing_and_ideas_citations_carry_the_signature_glyph(workspace, vtt_file):
    from openpod.catch import catch

    result = catch(
        "https://example.com/ep1", workspace=workspace, kind="podcast",
        transcript_path=str(vtt_file),
    )
    briefing = result.entry.briefing_path.read_text(encoding="utf-8")
    ideas = result.entry.ideas_path.read_text(encoding="utf-8")

    assert result.toc and result.ideas  # sanity: the fixture actually produced citations
    assert briefing.count("▸") >= len(result.toc)     # briefing.md cites the TOC
    assert ideas.count("▸") >= len(result.ideas)       # ideas.md cites the ideas

    attribution = "— cited with [OpenPod](https://github.com/openpodhq/openpod?ref=briefing)"
    assert attribution in briefing
    assert attribution in ideas


def test_json_outputs_carry_the_moment_display_field(workspace, vtt_file):
    """catch/search/export JSON all carry a preformatted "moment" field
    alongside the raw seconds — agents paste display fields verbatim, which
    is how the glyph reaches chat panes without any extra formatting work."""
    import json

    from openpod.catch import catch
    from openpod.exports import export_timestamps
    from openpod.search import search

    result = catch(
        "https://example.com/ep1", workspace=workspace, kind="podcast",
        transcript_path=str(vtt_file),
    )
    assert result.ideas and result.toc
    assert all(i.to_dict()["moment"].startswith("▸ ") for i in result.ideas)
    assert all(t.to_dict()["moment"].startswith("▸ ") for t in result.toc)

    hits = search("consensus", workspace=workspace)
    assert hits
    assert all(h.to_dict()["moment"].startswith("▸ ") for h in hits)

    payload = json.loads(export_timestamps(result.entry_id, workspace=workspace, fmt="json"))
    assert payload["segments"]
    assert all(seg["moment"].startswith("▸ ") for seg in payload["segments"])


def test_card_html_carries_ref_card_and_citation_hierarchy(workspace, vtt_file, tiny_wav_file):
    from openpod.catch import catch
    from openpod.clip import clip

    result = catch(
        "https://example.com/ep1", workspace=workspace, kind="podcast",
        transcript_path=str(vtt_file),
    )
    clip_result = clip(result.entry_id, 0.0, 5.0, workspace=workspace,
                       audio_path=str(tiny_wav_file))

    assert clip_result.card_path is not None
    assert clip_result.card_path.suffix == ".html"
    card_html = clip_result.card_path.read_text(encoding="utf-8")

    assert "?ref=card" in card_html   # WO-3 item 3: the k-proxy for R2, non-negotiable
    assert "▸" in card_html            # the signature survives in the card too

    # WO-3 item 4: source attribution (the eyebrow block) renders before —
    # i.e. visually above, given top-to-bottom document flow — the OpenPod
    # mark. Never invert this, even though it looks humble.
    eyebrow_pos = card_html.index("letter-spacing:0.24em")
    mark_pos = card_html.index("cited with")
    assert eyebrow_pos < mark_pos
