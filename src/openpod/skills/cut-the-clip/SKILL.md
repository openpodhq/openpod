---
name: Cut the Clip
description: Pull the shareable minute — a local, sentence-snapped clip plus a deep-link share card, with captions and labels as export derivatives.
version: "2"
primitives: [search, export_timestamps, clip, captions, settings, doctor, persona]
artifacts: [clips/]
---

# Cut the Clip

The user wants the shareable moment ("pull the 90 seconds where they compare
the two frameworks").

## The two artifacts — never confuse them

- **Library master** (`.openpod/library/…/clips/`): the clean cut. Canonical,
  citeable, never modified. This is the archive.
- **Export derivative** (working folder / `clip.export_dir`): the
  presentation copy — captions, name-plate label, social encode. This is
  what gets posted.

Never burn text into the library master. Never write your own scripts,
venvs, overlays, or preview frames under `.openpod/library/` — working files
belong in the export folder. If you're unsure whether the machine can burn
text at all, call `doctor` first (many ffmpeg builds can't).

## Steps

1. **Locate the span.** Use `search` (or `export_timestamps`) to find the
   moment; read `persona()` to judge what "shareable" means for this user.
   If the user's link carries a `t=` that doesn't match what they described,
   trust their description: search the transcript for the topic and confirm
   the span with a quote preview before cutting.
2. **Cut.** Call `clip(entry_id, start, end)`. OpenPod snaps to sentence
   boundaries, keeps video when the source has it, and writes the clean
   master + deep-link card into the entry's `clips/` directory.
3. **Presentation, only through the product.**
   - Captions: pass `captions="soft"` (sidecar) or `"burn"` with an
     `out_dir`. Caption lines come from the transcript — never hand-summarize
     them. If `translation_needed` is set (e.g. user prefers Hebrew),
     translate the sidecar **line-by-line, keeping every line and its
     timings**, then re-verify with `captions(verify_path=…)` — a coverage
     gap means spoken content was dropped; restore it before any burn.
   - Label ("who is speaking"): pass `label=true` to use the episode's
     structured speakers, or `label_text` the user approved. Never type a
     name from memory — that's how "Ross" becomes "Roth" in burned pixels.
   - Working copy: pass `out_dir` (or set `clip.export_dir` once via
     `settings`) so post-ready files land in the user's working folder, not
     the vault.
4. **Report the paths**, clearly separated: the library master, the share
   card, and the export derivatives. Surface `capability_note` verbatim —
   if the clip is audio-only or the ffmpeg build can't burn, the user hears
   it from you *before* they open the file.
5. If the span feels mid-sentence, re-cut with adjusted bounds rather than
   shipping a clip that starts on half a word.
6. If the user is heading toward *posting* this (captions, labels, hooks,
   translations, social encodes), hand off to **Make it Shareable** — that
   skill owns the export pipeline end-to-end.

## Never re-host

The clip is a private, user-owned file. Sharing happens via the deep-link
card, which points at the source — OpenPod never republishes audio. The
export derivative exists because users post anyway; keeping it separate
from the master is what protects the archive.
