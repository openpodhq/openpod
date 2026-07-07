---
name: Chapter It
description: Give me a navigable TOC — timed chapters with deep-links for a caught episode.
version: "1"
primitives: [export_timestamps, persona]
artifacts: []
---

# Chapter It

The user wants to navigate an episode, not read a summary ("give me chapters",
"a table of contents").

## Steps

1. Call `export_timestamps(entry_id)` — markdown for reading, json when
   another tool consumes it. Each segment carries a timestamp and a deep-link
   to that moment in the native player.
2. **Emphasize by interest.** Read `persona()` and bold or annotate the
   segments that match what the user amplifies, so the TOC reads as "your
   map of this episode", not a generic outline.
3. Present the TOC with clickable deep-links. If the user wants it saved,
   write it next to the entry's artifacts and report the path.
4. If the entry isn't caught yet, run `catch` first (that's the Catch Me Up
   skill's first step) and then chapter it.
