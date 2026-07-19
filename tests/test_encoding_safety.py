"""The CLI must survive a stdout that cannot encode the brand glyphs.

A Windows redirect falls back to cp1252 and a POSIX/C locale to ascii; neither
can carry ``▸``. Before enable_safe_output() existed, `openpod search` on such
a stdout printed nothing and exited 1 — the glyph in every hit took the whole
command down. NO_COLOR did not help: stripping ANSI leaves the glyphs.
"""

import io
import os
import subprocess
import sys

import pytest

from openpod import theme


@pytest.mark.parametrize("encoding", ["cp1252", "cp437", "ascii"])
def test_banner_encodes_after_guard(encoding):
    """The banner must survive every legacy stdout encoding."""
    stream = io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="strict")
    with pytest.raises(UnicodeEncodeError):
        stream.write(theme.banner())  # unguarded: this is the bug

    stream = io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="openpod_ascii")
    stream.write(theme.banner())  # guarded: must not raise
    stream.flush()


def test_ascii_fallbacks_are_length_preserving():
    """The banner box only stays aligned if each mapping is a single char."""
    banner_glyphs = set("▸┌┐└┘─│·—")
    for ch in banner_glyphs:
        assert len(theme._ASCII_FALLBACKS[ch]) == 1, (
            f"{ch!r} maps to a multi-char string; the banner box would misalign"
        )


def test_banner_box_stays_aligned_in_ascii():
    """All three box lines must keep equal width after transliteration."""
    ascii_banner = theme.banner().encode("ascii", errors="openpod_ascii").decode("ascii")
    lines = [l for l in ascii_banner.split("\n") if l.strip().startswith(("+", "|"))]
    assert len(lines) == 3, f"expected 3 box lines, got {len(lines)}"
    assert len({len(l) for l in lines}) == 1, f"box misaligned: {[len(l) for l in lines]}"


def test_cli_search_survives_cp1252_stdout():
    """End-to-end: the real entry point on a real cp1252 stdout.

    Runs a subprocess because the guard rewires sys.stdout, which pytest owns.
    """
    code = (
        "import sys; from openpod.cli import main; "
        "sys.argv = ['openpod', '--help']; "
        "sys.exit(main())"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        # Inherit the environment (Windows Python won't even start without
        # SystemRoot); only the stdout encoding is forced.
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
    )
    assert b"UnicodeEncodeError" not in proc.stderr, proc.stderr.decode("utf-8", "replace")
