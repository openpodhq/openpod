# Contributing to OpenPod

Thanks for helping! OpenPod is a local-first tool with a few hard invariants —
please keep them in mind, they're the whole point of the project.

## The invariants (non-negotiable)

1. **Local-pure.** No code path may upload the user's audio, transcripts,
   library, or persona to any server. If a feature needs a server, it's Stage 2
   and doesn't belong here.
2. **Pull, not push.** No background processes, no always-on monitoring.
3. **Artifacts, not telemetry.** Persist what the user explicitly produces.
   Never capture behavioral signal (play/skip/replay).
4. **Navigate, don't re-host.** Sharing is deep-links to the moment in the
   native player, never republished audio/video.

If a change touches these, open an issue first.

## Dev setup

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

CI runs the suite on Linux, Windows, and macOS — keep changes portable
(explicit `encoding="utf-8"` on text I/O, no POSIX-only permission
assertions outside an `os.name == "posix"` gate, pathlib over string paths).

The test suite must stay **fully offline** — no network calls, no model
downloads. Use fixtures (see `tests/conftest.py`). Heavy/network dependencies
(`yt-dlp`, `youtube-transcript-api`, `faster-whisper`, `mcp`) are optional
extras and must be imported lazily so the core installs and tests without them.

## Style

- Standard library first; add a dependency only when it clearly earns its place.
- Keep the public surface small (`catch`, `clip`, `export_timestamps`, `search`,
  plus `follow`/`persona`).
- New optional integrations go behind an extra in `pyproject.toml` and a lazy
  import with a clear "install `openpod[...]`" error message.

## License

By contributing you agree your contributions are licensed under
**MIT**, the project's license.
