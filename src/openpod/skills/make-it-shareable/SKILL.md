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
   `clip.label_template`. If `export_dir` is unset, ask the user for their
   working folder once and save it via `settings` — never default to the
   library.

## The pipeline (captions are data before they are pixels)

1. **Cut or reuse the master.** `clip(entry, start, end, captions="soft",
   out_dir=…)` — the library keeps the clean cut; the working folder gets
   copies plus `deeplink.txt`. The clip's `.json` now carries a structured
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
   `captions="burn"`, `out_dir`, and `captions_file=<verified .srt>`. The
   burn refuses unverified translations and impossible capability, and the
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
