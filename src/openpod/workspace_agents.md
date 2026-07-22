# OpenPod — how to work in this workspace

This folder is an OpenPod workspace. `.openpod/` is the user's library —
a private, permanent archive of everything they've caught. Files made
for posting are **export derivatives** that live outside it. OpenPod is
the toolkit (the `openpod` CLI and the MCP tools); the packaged skills
are the product playbooks. Presentation work done here without reading
them rebuilds a worse version of the product beside the product.

## Who decides what

**The user decides** anything that ends up in visible pixels or shapes
their disk: where exports land, captions off/soft/burned, caption color
and style, clip dimensions, headline plates, speaker labels. When a
result carries `first_use`, those are the user's setup questions —
relay them as one multiple-choice message, save the answers with
`openpod settings <key> <value>`, and set `clip.setup_done true` only
after the user has picked or explicitly skipped (skipping keeps
defaults). Never answer them on the user's behalf.

**You decide** the mechanics: which command to run, how to find the
moment, how to phrase the report. Anything the user won't see or keep
defaults silently — the product never blocks you on trivia.

## Presentation goes through the product

Burned captions with word styling (plain / keyword / marker / karaoke),
aspect crops (vertical 9:16 — TikTok/Reels/Shorts, square 1:1 — feed
posts, wide 16:9 — YouTube/X), speaker name plates, verified
line-by-line translation: all built in, styled from the
`clip.caption_style` settings. Never hand-roll ffmpeg, ASS, or
image-overlay scripts for any of this. If something seems impossible,
run `openpod doctor` — it's a capability question, not an improvisation
prompt.

## The playbooks

`openpod skills` lists them; `openpod skills <slug>` prints one. Read
the relevant skill before any multi-step flow:

- **find-the-moment / cut-the-clip** — locate and cut the span
- **make-it-shareable** — captions, labels, and post-ready exports
- **set-up-my-persona / sharpen-my-persona** — what "interesting" means
  for this user

## Non-negotiable hygiene

- Never write anything under `.openpod/library/` — masters stay clean;
  working files belong in the export folder.
- Never type a speaker's name from memory — labels come from structured
  metadata (`speakers.yaml`) or from the user.
- Surface `capability_note` from any result to the user verbatim.
- Keep the deep-link card with every export; never re-host the audio.
