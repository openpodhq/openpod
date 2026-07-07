"""Guardrail tests for the brand theme module (WO-5).

Covers the WO-1 acceptance criteria: NO_COLOR/pipe purity, the color
capability ladder matching brand-tokens.json exactly, moment() formatting,
and well-formed OSC 8 hyperlink wrapping.
"""

import inspect
import sys

from openpod import theme

ESC = "\x1b"


def _set_tty(monkeypatch, is_tty: bool) -> None:
    monkeypatch.setattr(sys.stdout, "isatty", lambda: is_tty)


def _clear_theme_env(monkeypatch) -> None:
    for var in ("NO_COLOR", "OPENPOD_NO_COLOR", "COLORTERM", "TERM", "OPENPOD_THEME"):
        monkeypatch.delenv(var, raising=False)


def _all_output(seconds=754, url="https://example.com/ep1?t=754") -> str:
    return "".join([
        theme.banner(),
        theme.moment(seconds, url),
        theme.path("/a/b/briefing.md"),
        theme.ok("caught: show/ep"),
        theme.fail("nope"),
        theme.dim("v0.1 · footer"),
    ])


class TestPurity:
    def test_non_tty_emits_zero_escape_codes(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        _set_tty(monkeypatch, False)
        assert ESC not in _all_output()

    def test_no_color_emits_zero_escape_codes(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        _set_tty(monkeypatch, True)
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.setenv("COLORTERM", "truecolor")
        assert ESC not in _all_output()

    def test_no_color_accepts_any_value(self, monkeypatch):
        # per the NO_COLOR spec, presence disables color — not truthiness.
        _clear_theme_env(monkeypatch)
        _set_tty(monkeypatch, True)
        monkeypatch.setenv("NO_COLOR", "")
        monkeypatch.setenv("COLORTERM", "truecolor")
        assert ESC not in _all_output()

    def test_openpod_no_color_flag_disables_styling(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        _set_tty(monkeypatch, True)
        monkeypatch.setenv("OPENPOD_NO_COLOR", "1")
        monkeypatch.setenv("COLORTERM", "truecolor")
        assert ESC not in _all_output()

    def test_no_color_output_byte_identical_to_piped_output(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        _set_tty(monkeypatch, False)
        piped = _all_output()

        _clear_theme_env(monkeypatch)
        _set_tty(monkeypatch, True)
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.setenv("COLORTERM", "truecolor")
        no_color = _all_output()

        assert piped == no_color


class TestColorCapabilityLadder:
    def test_truecolor_matches_token_dark(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        _set_tty(monkeypatch, True)
        monkeypatch.setenv("COLORTERM", "truecolor")
        expected = f"{ESC}[{theme._TOKENS['color']['blue_dark']['ansi_24bit']}m"
        assert expected in theme.moment(41)

    def test_truecolor_24bit_alias_and_light_variant(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        _set_tty(monkeypatch, True)
        monkeypatch.setenv("COLORTERM", "24bit")
        monkeypatch.setenv("OPENPOD_THEME", "light")
        expected = f"{ESC}[{theme._TOKENS['color']['blue_light']['ansi_24bit']}m"
        assert expected in theme.moment(41)

    def test_256_color_fallback_matches_token(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        _set_tty(monkeypatch, True)
        monkeypatch.setenv("TERM", "xterm-256color")
        expected = f"{ESC}[38;5;{theme._TOKENS['color']['blue_dark']['ansi_256']}m"
        assert expected in theme.moment(41)

    def test_unsupported_terminal_drops_color_but_keeps_glyph_and_link(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        _set_tty(monkeypatch, True)
        monkeypatch.setenv("TERM", "dumb")
        out = theme.moment(41, "https://example.com")
        assert "38;2" not in out
        assert "38;5" not in out
        assert theme.GLYPH in out
        assert "]8;;https://example.com" in out

    def test_default_theme_variant_is_dark(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        _set_tty(monkeypatch, True)
        monkeypatch.setenv("COLORTERM", "truecolor")
        expected = f"{ESC}[{theme._TOKENS['color']['blue_dark']['ansi_24bit']}m"
        assert expected in theme.moment(41)


class TestMomentFormatting:
    def test_mmss_under_an_hour(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        _set_tty(monkeypatch, False)
        assert theme.moment(65) == f"{theme.GLYPH} 1:05"
        assert theme.moment(0) == f"{theme.GLYPH} 0:00"

    def test_hhmmss_over_an_hour(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        _set_tty(monkeypatch, False)
        assert theme.moment(3725) == f"{theme.GLYPH} 1:02:05"

    def test_negative_seconds_clamp_to_zero(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        _set_tty(monkeypatch, False)
        assert theme.moment(-5) == f"{theme.GLYPH} 0:00"


class TestOSC8Hyperlink:
    def test_well_formed_when_url_given(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        _set_tty(monkeypatch, True)
        monkeypatch.setenv("COLORTERM", "truecolor")
        url = "https://example.com/ep1?t=754"
        out = theme.moment(754, url)
        assert out.startswith(f"{ESC}]8;;{url}{ESC}\\")
        assert out.endswith(f"{ESC}]8;;{ESC}\\")
        assert out.count("]8;;") == 2  # one open (with url), one close (bare)

    def test_no_hyperlink_when_url_omitted(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        _set_tty(monkeypatch, True)
        monkeypatch.setenv("COLORTERM", "truecolor")
        assert "]8;;" not in theme.moment(754)

    def test_plain_fallback_puts_url_beside_glyph(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        _set_tty(monkeypatch, False)
        url = "https://example.com/ep1?t=754"
        out = theme.moment(754, url)
        assert out == f"{theme.GLYPH} 12:34 {url}"
        assert ESC not in out


def test_no_public_function_takes_a_color_argument():
    for name in ("moment", "path", "ok", "fail", "banner", "dim"):
        params = inspect.signature(getattr(theme, name)).parameters
        assert "color" not in params, f"{name}() must not take a color argument"
