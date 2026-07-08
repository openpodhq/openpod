# Changelog

All notable changes to OpenPod are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions follow SemVer.

## [0.1.0] — unreleased

First Stage 1 alpha: local-pure, pull-only.

### Added
- **Deterministic cross-platform resolution** — episode identity is product
  code now, not per-session agent improvisation:
  - **Apple Podcasts ingest** (`ingest/apple.py`): `id{show}` + `i={episode}`
    + country parsed from the URL, resolved via the keyless iTunes Lookup API
    to the RSS feed and the episode's guid — the exact episode, never
    "newest in feed".
  - **Spotify ingest** (`ingest/spotify.py`): oEmbed title → iTunes episode
    search → guid-anchored feed match. No HTML scraping, no open-ended web
    search; Spotify-exclusives fail with a structured, actionable error.
  - **Identity crosswalk** (`crosswalk.py`, `EpisodeIdentity`): per-episode
    records under `.openpod/crosswalk/` mapping YouTube/Spotify/Apple/RSS ids
    with per-field provenance, plus a show-level table. Resolved once, cached
    forever; schema is user-free so it can later be served globally.
  - **Confirmation gate**: fuzzy matches below confidence 0.8 raise/return a
    structured `needs_confirmation` candidate instead of silently ingesting;
    `catch(..., confirmed=true)` records the user's confirmation. Confidence
    is corroborated (title + duration + date), never a single signal.
  - **No silent defaults**: unclassifiable links are `unknown` and return a
    structured `unresolved_link` error (with what was tried), instead of
    falling through into the RSS parser.
- **Origin/source decoupling + capability-aware links** — `catch` persists
  what the user pasted (`origin`) separately from the transcript source;
  `build_link()` renders the output on the user's platform (explicit arg →
  `preferred_playback_app` setting → origin) from a per-app capability table
  and reports honestly when it degrades ("Apple opens the episode, not the
  moment"). New MCP tools: `playback_link`, `set_preferred_app`.
- **Cross-source timestamp alignment** (`align.py`) — piecewise-constant
  offset maps recovered by probe-and-bisect (≈10–20 fifteen-second ASR
  probes per episode, O(log n) per ad break), cached on the crosswalk record.
- **Content-type validation** (`ingest/validate.py`) — HEAD + magic-byte
  sniffing with a defined support matrix; tracking redirectors resolve to
  their terminal URL once; unsupported content raises `unsupported_format`
  instead of being piped into Whisper on luck.
- **Shared media cache + video-preserving clips** — one download per episode
  under `.openpod/media/`; `clip` keeps video by default when the source has
  it (`--video` / `--audio-only` on the CLI, `video=` on MCP) and reports
  `has_video` + a capability note *before* delivering a mismatched artifact.
- **Follow-time show resolution** — following an Apple show URL resolves and
  stores its RSS feed once, recorded in the crosswalk show table.

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
