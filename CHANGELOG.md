# Changelog

All notable changes to OpenPod are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions follow SemVer.

## [Unreleased]

### Added
- **`openpod init` — the workspace front door.** Creates `.openpod/` and
  writes `AGENTS.md` at the workspace root: the agent contract — who
  decides what (everything that lands in visible pixels belongs to the
  user; `first_use` questions are relayed, never self-answered),
  presentation goes through the product (no hand-rolled ffmpeg/ASS
  scripts beside the built-in styles), and where the playbooks are
  (`openpod skills`). An existing AGENTS.md is never clobbered —
  `--print` to merge by hand, `--force` to overwrite. Field-tested need:
  an agent in an undocumented workspace scaffolded its own README,
  answered the user's setup questions itself, and reimplemented caption
  styling the engine already ships.

### Changed
- **First-use speaks to the agent, not past it.** The CLI's first-clip
  hint used to read as two commands to run — and an agent driving the
  CLI ran them: it answered the user's one-time setup questions itself
  and set `clip.setup_done` without the user ever seeing a choice. The
  hint now renders the full multiple-choice block addressed to the
  reader that actually reads it ("these are your user's decisions, not
  yours"), and the `first_use` payload and MCP clip tool carry the same
  contract: relay, save, and only the user may skip.

### Added
- **First-use clip setup + social framing.** The first clip in a workspace
  returns a `first_use` block: the product's own multiple-choice questions,
  asked once in one message — where clips land (the `.openpod` library, the
  working directory, or a named folder), captions burned/soft/off (burned
  listed first, it's what social feeds expect), caption color, caption
  style (boxed strip or outlined text), clip dimensions, and whether to
  burn a headline plate — saved via settings, then `clip.setup_done` —
  never asked twice, skipping keeps defaults. The `"ask"` export-dir
  sentinel is never treated as a folder name at cut time. New `clip.aspect` presets crop the **export
  derivative only** (vertical 9:16 TikTok/Reels/Shorts, square 1:1, wide
  16:9 — the library master always keeps source dimensions), and
  `clip.caption_style` (font/color/outline/boxed/position) styles burned
  captions via libass force_style — a subtle branded default, never a
  watermark. Burn-gate refinement: a *proven* language mismatch still
  refuses to burn; an unlabeled caption language burns with a
  check-the-frames note instead of blocking. `--aspect` on the CLI,
  `aspect` on MCP clip. Also fixed: a video file passed via `audio_path`
  now counts as video.

### Added
- **Caption styles: keyword / marker / karaoke.** The burn now writes an
  `.ass` (part of the export package) instead of driving libass with one
  uniform `force_style` per line — the capability every named style hinges
  on is a differently-colored word, which SRT can't express. `clip.style`
  (long reserved in settings, read nowhere) is now wired: `keyword` and
  `marker` render the agent-marked `*word*` in `caption_style.keyword_color`
  (brand blue by default) — marked per line by the agent in the captions
  file, the ‖ contract extended, never guessed (and for RTL text, not
  guessable); `karaoke` lights words as they're spoken, consuming the
  word-level track (`word_level=True`), degrading to keyword styling with a
  note when no track exists. `caption_style` gains `keyword_color`,
  `weight`, and `shadow`; `‖` in a burned line now renders as a real line
  break. `--style` on the CLI, `style` on MCP clip.
- **`transcript.md` — the reading view of every transcript.** Written by
  `catch` next to `transcript.json`, from the same pass: cues reflowed into
  ~20–42s paragraphs (sentence-boundary merging; speaker changes, chapter
  boundaries, and long pauses break unconditionally), chapters as `##`
  headings so the host editor's outline pane becomes the chapter rail, and
  one blue `▸ m:ss` badge per paragraph deep-linking the player at that
  moment — reference-style, so the link table pools at the bottom and the
  raw file stays readable. Byte-identical for identical input (safe in
  git); overwritten wholesale on re-catch; entries that can't link (no
  feed/guid/episode-key) degrade to plain `` `▸ m:ss` `` badges instead of
  dead links. New `openpod render <entry>` re-renders it from the existing
  `transcript.json` — no re-fetch, no re-transcription.

### Changed
- **Share card: the blue `▸ m:ss` badge is the deep link itself** — the
  "open at m:ss in the episode" caption is gone. The signature is always
  blue and always live; it doesn't need a label saying so.
- **"Captions not burned" now says so — loudly and first.** When ffmpeg
  lacks libass, `clip` still can't burn styled captions (it writes a plain
  `.srt` sidecar players render in their own generic style). That degrade
  note now *leads* the `capability_note` instead of trailing behind minor
  notes, names the cause, and points to the fix. The README install section
  documents the `libass`/`drawtext` requirement and how to get a
  caption-capable ffmpeg per platform — notably that the default macOS
  `brew install ffmpeg` is now a minimal build (use `ffmpeg-full`).

### Fixed
- **RTL captions no longer flip.** A burned Hebrew/Arabic line that opened
  with a Latin word or number ("Claude Max", "API", "STEM") inherited
  left-to-right base direction and the whole line reversed — right-aligned
  text sliding left, punctuation on the wrong side. Every burned line is now
  wrapped, per visual row, in RLE…PDF bidi marks that pin the base direction
  to RTL, so Latin/number runs render as correct LTR islands. The same marks
  now ride in the soft `.srt`/`.vtt` sidecar too (for RTL-source captions), so
  players that run their own bidi don't flip it either; the burn strips any
  such marks before re-rendering, so a sidecar it reads is never double-wrapped.
  RTL burns also default to a Hebrew-capable face (Rubik) instead of Arial when
  no `caption_style.font` is set — scoped to RTL, so the LTR look is unchanged;
  the note names Rubik/Heebo/Assistant if glyphs are still missing.
- **No more crash on a stdout that can't encode the brand glyphs.**
  `openpod search` (and any other command) printed nothing and exited 1 on
  a cp1252 or ascii stdout — a Windows redirect, a POSIX/C locale in CI.
  One codec error handler now degrades the glyphs 1:1 to ASCII (the banner
  box stays aligned) and only rewires streams that genuinely can't carry
  them; UTF-8 terminals are untouched.
- **Local-file catches have an identity.** Every file-kind catch used to
  slug to `file/episode` — no title, no path recorded — so a second local
  file silently replaced the first, and `clip` demanded `audio_path=` for
  media sitting exactly where the user said it was. The resolved path and
  filename are now recorded (media is used in place — never copied, never
  fetched), and the library refuses to merge provably different sources:
  the newcomer gets a numbered sibling (`file/interview-2`). Re-catches of
  the same source still regenerate in place.
- **`openpod-mcp` without the `mcp` extra** prints the install hint alone
  on stderr and exits 1, instead of burying it under a chained traceback.

## [0.1.0] — 2026-07-16

First Stage 1 alpha: local-pure, pull-only.

### Added
- **Two-layer persona: who you are vs. what this folder is for.** Global
  (`~/.openpod/persona.md`): role, language & style, standing
  interests/filters, what you want from long-form — true in every
  workspace, the layer a cloud tier would sync. Workspace
  (`.openpod/persona.md`): current projects, local amplify/filter topics,
  and the machine-derived blocks (derived from *this* library). The MCP
  `persona` tool returns both layers labeled — identity/language lean
  global, relevance decisions lean workspace — and routes writes by kind.
  Existing single-file personas get a **split proposal**: sections are
  classified (Role → global, Current projects → local, unknowns → ask) and
  offered as one multiple-choice confirmation — the user picks, never
  writes prose — then `persona_split` / `openpod persona split --to-global`
  moves exactly the confirmed sections. Machine-owned blocks are never
  offered and never move. CLI: `persona init|show --global`, `persona
  split`. House rule codified in the skill: users choose from options
  mined from evidence; system files never demand elaboration.
- **`summary.md` — the cross-session memory artifact**: a durable, local
  distillation of an episode, written by the agent (or user) after actually
  engaging with it, and loaded by future sessions as context. The contract
  is deliberately minimal: OpenPod owns placement and identity frontmatter
  (entry_id, episode_key — sync-ready for the cloud tier, updated_at,
  revision count); the body — structure, language, depth, relevance — is
  entirely the author's. `write_summary(append=True)` lets sessions add
  dated takes without losing prior ones. Surfaces: MCP `save_summary` +
  `recall_summaries` (the "load what past sessions learned" call),
  summary included in `get_briefing`, CLI `openpod summary` (read / write
  / list), and the **Remember This** skill — guidance (personalize from
  the session, exclude what didn't matter, cite moments, budget tokens),
  explicitly defaults-not-mandates.
- **No more silent Whisper fallback** — a cost-based gate keeps interaction
  fast: every ASR run is *announced* (why captions failed, the wall-clock
  estimate, and that it's local compute — no tokens or API cost — recorded
  in `transcript.notes`), but the user is only *asked* when it's worth
  asking. Caption failures are classified: permanent (no captions exist →
  ASR just runs, announced) vs transient (429/IP throttling → a short
  transcription under `asr.auto_threshold_seconds` (default 180s) runs with
  a notice, a long one raises structured `captions_unavailable` with both
  options priced — wait and re-catch for free captions, or `asr="now"` /
  `--asr-now`). `--no-asr` never transcribes. Throttled-then-ASR
  transcripts carry "a later re-catch may upgrade this to platform
  captions". RSS enclosure ASR announces itself the same way. Caption
  language follows `locale.preferred_language` (a Hebrew interview fetches
  Hebrew captions first, then fallback, then English).
- **Caption data contract + export package** (naval-clipper design port —
  contracts, not renderers):
  - Clips carry a **structured caption track in their `.json`**: phrases
    chunked by the karaoke rules (≤5 words / sentence / `‖` force-break —
    the marker translated Hebrew keeps so nothing regroups it), language +
    `rtl`, and honest `timing` (`approximate` for rolling YouTube cues,
    `exact` only with word-level).
  - **Word-level track** (`--word-level` on `clip` and `captions`, asr
    extra): whisper on just the clip window/file → `*.words.json` — the
    karaoke-highlight layer.
  - **Burn gate**: burning refuses unverified translations and any
    `captions_file` that fails the coverage check (dropped speech named in
    the note); RTL burns warn to eyeball `verify.png` for missing glyphs.
  - **Export package**: the working folder now also gets `deeplink.txt`,
    `label.json` (label + optional `hook`), `*.words.json`, and `verify.png`
    (start/mid/end frames of the burned derivative, tiled).
  - **Speakers**: `{title}`/`{role}` template aliases and a workspace
    `speakers.yaml` (keyed by show) as fallback — teach OpenPod a show's
    hosts once.
  - **`clip.style` setting** (default `karaoke`) reserved for renderers.
  - **New skill: Make it Shareable** — the export pipeline end-to-end
    (doctor first, captions as data, line-locked translation, verified burn,
    export-folder hygiene); `cut-the-clip` hands off to it, and
    `sharpen-my-persona` may offer `locale.preferred_language` when the
    library skews non-English.
- **The two-artifact clip contract** (QA session 2026-07-09): the library
  clip is the clean master and is never mutated; presentation — captions,
  name-plate labels, social encodes — are **export derivatives**:
  - **Settings schema v0** (`settings.yaml`, documented defaults merged at
    read time): `locale.preferred_language`/`fallback_language`,
    `clip.captions` (`off|soft|burn`), `clip.caption_language`,
    `clip.burn_in` (default **false**), `clip.label`, `clip.label_template`,
    `clip.export_dir`, `clip.keep_clean_master`. Unknown keys are rejected
    with the documented list. CLI `openpod settings`, MCP `settings` tool.
  - **Caption primitive** (`captions.py`): SRT/VTT sidecars generated from
    `transcript.window(start, end)` — full coverage by construction — plus a
    **coverage verify** (`openpod captions --verify`, MCP `captions`) that
    catches dropped lines in edited/translated caption files before any
    burn-in. Translation is flagged (`translation_needed`), never guessed.
  - **Clip presentation flags**: `--captions off|soft|burn`, `--lang`,
    `--label` / `--label-from-meta`, `--out DIR` (and `clip.export_dir`):
    derivatives and copies land in the working folder; burned files land
    *only* there. Labels render from structured `source.speakers`
    (name/role), never from agent memory.
  - **`openpod doctor`**: ffmpeg capability report (libass/drawtext probed
    before any burn is promised), settings status, and library hygiene —
    foreign files (venvs, scripts, previews) under `library/` are flagged
    and quarantined to `library/_scratch/` with `--fix`.
  - **cut-the-clip skill v2**: teaches the two-artifact contract — never
    burn the master, captions from transcript with verify, labels from
    meta, working files never in the vault.
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
- MIT license; offline test suite; CI across Python 3.10–3.13.
