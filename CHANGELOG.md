# Changelog

All notable changes to OpenPod are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions follow SemVer.

## [0.1.0] — unreleased

First Stage 1 alpha: local-pure, pull-only.

### Added
- **`catch`** — ingest a podcast/RSS/YouTube link (or local file) into a local
  library: timed transcript, extracted key ideas, navigable TOC, and a briefing
  scaffold for the agent to complete.
- **`search`** — cross-episode local search over the whole library (SQLite FTS5
  keyword retrieval + a dependency-free local embedding re-rank).
- **`export_timestamps`** — emit timed segments + deep-links as JSON or Markdown.
- **`clip`** — sentence-snapped, local clip extraction via ffmpeg, plus a
  shareable deep-link card. Local files only; no re-hosting.
- **`follow` / `digest`** — local follow list (podcast RSS + YouTube channels)
  and a "what's new" digest polled locally.
- **`persona`** — a local, user-owned `persona.md` that evolves from the library.
- **MCP server** (`openpod-mcp`) exposing the primitives to AI agents.
- Transcript parsers for WebVTT, SubRip, YouTube `json3`, and cue lists.
- Deep-link construction for YouTube, Spotify, and open podcast enclosures.
- AGPL-3.0-or-later license; offline test suite; CI across Python 3.10–3.13.
