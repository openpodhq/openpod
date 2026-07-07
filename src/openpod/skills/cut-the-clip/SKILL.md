---
name: Cut the Clip
description: Pull the shareable minute — a local, sentence-snapped clip plus a deep-link share card.
version: "1"
primitives: [search, export_timestamps, clip, persona]
artifacts: [clips/]
---

# Cut the Clip

The user wants the shareable moment ("pull the 90 seconds where they compare
the two frameworks").

## Steps

1. **Locate the span.** Use `search` (or `export_timestamps`) to find the
   moment; read `persona()` to judge what "shareable" means for this user —
   the decision, the number, the named tool, not the throat-clearing.
2. **Cut.** Call `clip(entry_id, start, end)`. OpenPod snaps to sentence
   boundaries, cuts a local media file, and writes a deep-link share card
   into the entry's `clips/` directory. (Requires ffmpeg.)
3. **Report the paths**: the clip file, the `.card.html` share card (plus
   `.card.png` when a headless renderer is installed), and the deep-link.
   The card — not the media — is what they paste into Slack; it navigates
   the recipient to the moment in the native player.
4. If the span feels mid-sentence, re-cut with adjusted bounds rather than
   shipping a clip that starts on half a word.

## Never re-host

The clip is a private, user-owned file. Sharing happens via the deep-link
card, which points at the source — OpenPod never republishes audio.
