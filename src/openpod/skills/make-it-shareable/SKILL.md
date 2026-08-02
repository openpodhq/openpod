---
name: Make it Shareable
description: Turn a clean library clip into a post-ready export — captions, speaker label, working folder, verify frames — without ever touching the master.
version: "1"
primitives: [clip, captions, settings, doctor, persona]
artifacts: [exports/]
---

# Make it Shareable

The user has (or wants) a clip and intends to *post* it. This skill owns the
presentation layer; "Cut the Clip" owns the cut. OpenPod owns caption data,
speaker identity, locale, and export hygiene — cinematic rendering belongs
to downstream tools.

## Before anything

1. Call `doctor` once: can this machine burn text at all (libass/drawtext)?
   If not, the deliverable is soft captions + sidecars — say so up front.
2. Read `settings`: `locale.preferred_language`, `clip.export_dir`,
   `clip.aspect`, `clip.caption_style`, `clip.label`,
   `clip.label_template`. Once first-use has run (`clip.setup_done`), an
   unset `export_dir` means the user CHOSE the library — respect it, don't
   re-ask. An `export_dir` of `"ask"` means the interface still owes them
   the folder-name question: ask once, save the real path via `settings`.
3. **First use**: if a clip result carries `first_use`, this workspace has
   never set its clip defaults. Ask its questions ONCE — one message, pure
   multiple choice, in its order: where clips land (`.openpod` library /
   the working directory / a named folder), captions burned/soft/off,
   caption color, caption style (boxed or outlined), clip dimensions per
   platform, and whether to burn a headline plate — save the answers via
   `settings`, set `clip.setup_done=true`, and never ask again. A user
   posting to social almost always wants **burned** captions and a
   platform shape; don't silently hand them a sidecar they can't see.

## The pipeline (captions are data before they are pixels)

1. **Cut or reuse the master.** `clip(entry, start, end, captions=…,
   aspect=…, out_dir=…)` — the library keeps the clean cut at source
   dimensions; the working folder gets copies plus `deeplink.txt`, and the
   social derivative is cropped to `aspect` (vertical/square/wide) with
   captions styled per `clip.caption_style`. The clip's `.json` now carries a structured
   caption track: phrases with `break` semantics (`sentence` / `length` /
   `force`), language, `rtl`, and honest `timing` ("approximate" for rolling
   platform cues).
2. **Word-level (karaoke highlight), when asked**: `word_level=true` adds a
   word-timing track from the cut clip itself (needs the asr extra); it
   lands in the clip json and as `*.words.json` in the export folder.
3. **Label**: `label=true` renders from structured speakers (episode meta or
   the workspace's `speakers.yaml`). No structure → OpenPod refuses to
   guess; ask the user, then record it in `speakers.yaml` so it's known
   forever. Never type a name from memory.
4. **Translation** (e.g. `preferred_language: he`): the sidecar comes out in
   the source language with `translation_needed` set. Translate it
   **line-by-line** — keep every line and its timings, use `‖` where a
   visual break must survive regrouping. Then verify:
   `captions(entry, start, end, verify_path=<translated.srt>)`. Gaps mean
   dropped speech; restore before going further.
5. **Burn (only on request, only as a derivative)**: re-run `clip` with
   `captions="burn"`, `out_dir`, and `captions_file=<verified .srt>`. On a
   workspace that never ran first-use setup the burn is REFUSED with
   `needs_decision` — no file until the user has answered the setup
   questions (present them, save, `clip.setup_done=true`, re-run; only
   the user may skip). The burn also
   refuses unverified translations and impossible capability, and the
   social file lands only in the export folder. For RTL burns, open
   `verify.png` (start/mid/end frames) and actually look — boxes instead of
   Hebrew glyphs means the font needs fixing, and the user hears that from
   you, not from a commenter.
6. **Report** the export folder inventory and what each file is for. The
   post text itself stays out of band (persona / posting tools) — in-video
   text and platform caption are different surfaces.

## Hygiene (non-negotiable)

Everything you make lives in the export folder. Never write scripts, venvs,
overlays, or previews under `.openpod/library/` — if you catch yourself
reaching for Pillow because ffmpeg can't burn, stop and deliver soft
captions with the capability note instead. `doctor --fix` exists because an
agent once didn't.
