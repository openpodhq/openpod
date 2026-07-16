"""The ``openpod-mcp`` entry point must fail helpfully, not loudly.

When the ``mcp`` extra is missing, the user's first contact with the server
is the error message — it has to be the install hint alone (CONTRIBUTING's
"clear install error" bar), not a chained traceback, and the process must
exit non-zero so agent launchers notice.
"""

import pytest

from openpod import mcp_server


def test_missing_extra_prints_hint_without_traceback(monkeypatch, capsys):
    def boom():
        raise RuntimeError(
            "The MCP server needs the 'mcp' package. Install with:\n"
            "    pip install 'openpod[mcp]'"
        )

    monkeypatch.setattr(mcp_server, "build_server", boom)
    with pytest.raises(SystemExit) as exc:
        mcp_server.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "openpod[mcp]" in err
    assert "Traceback" not in err
