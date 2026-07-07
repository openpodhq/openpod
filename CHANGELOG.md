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
  and a "what's new" digest polled locally. Digest items carry
  `in_rotation`: follows the user has never caught are flagged as the
  **discovery pool** — the What's New skill surfaces interest-matching
  episodes from unfamiliar shows with a cheap trial (briefing or the one
  matching beat) instead of burying them under familiar names. Persona
  filters apply to topics, never to sources.
- **`persona`** — a local, user-owned `persona.md` that evolves from the
  library. Machine-owned blocks (`## Derived from my library`, `## Imported
  interests (opt-in)`) are regenerated in place under a marker contract that
  provably never touches the human-authored sections.
- **`import`** — opt-in OPML import: stages the raw export verbatim in
  `.openpod/imports/`, merges subscriptions into `follows.yaml` de-duplicated
  and tagged with `source:` provenance, and refreshes the persona's
  imported-interests block. Snapshot, not sync; no OAuth, no cloud APIs.
- **`note`** — append to an episode's user-owned `notes.md` (also exposed to
  agents as `append_note`, append-on-request only).
- **`persona scan`** — a fast, bounded, read-only workspace evidence sweep
  (project folders by recency, markdown doc titles, `CLAUDE.md` headings,
  library themes, follows) powering a **guess-and-confirm interview**: the
  agent presents multi-select options mined from the evidence and the user
  picks instead of typing. Extra roots are opt-in only. The trust-question
  was retired (follows/OPML answer it); all interview questions are now
  multi-select guesses with a free-text escape hatch.
- **Skills catalog** — nine versioned `SKILL.md` bundles shipped in the package
  (Catch Me Up, Set Up My Persona, Bring In My World, Sharpen My Persona, Find
  the Moment, Cut the Clip, What's New, Chapter It, Follow This); `openpod
  skills` lists them and the MCP server exposes them as prompts.
- **MCP server** (`openpod-mcp`) exposing the primitives to AI agents, plus
  `list_entries`, `append_note`, and `import_opml`; every tool result carries
  the entry id and the path(s) touched.
- **CLI UX contract** — mutating commands print the path(s) they wrote;
  read/produce commands take `--json` shaped identically to the MCP tools;
  errors name the fix; `catch` on a persona-less workspace suggests "Set Up My
  Persona".
- Transcript parsers for WebVTT, SubRip, YouTube `json3`, and cue lists.
- Deep-link construction for YouTube, Spotify, and open podcast enclosures.
- **The anchor ladder** — every idea, search hit, and TOC entry carries up to
  three labeled deep-links, because different readers want different
  landings: the creator's **chapter** ("take me to the topic"), the detected
  **beat** where the idea starts being articulated ("play the argument from
  its start"), and the exact **moment** ("show me where they said it").
  Beats come from creator chapters when the source ships them (YouTube
  chapter markers, or timestamp lists parsed from the description) — with
  over-long chapters sub-segmented by local lexical-cohesion topic detection
  — and from pure topic detection otherwise. No link ever lands mid-sentence
  without a labeled alternative. Beats persist in `meta.json`; hits carry
  `chapter_*`, `segment_*`, and `deeplink` fields; `ideas.md` renders the
  ladder with a one-line legend.
- AGPL-3.0-or-later license; offline test suite; CI across Python 3.10–3.13.
