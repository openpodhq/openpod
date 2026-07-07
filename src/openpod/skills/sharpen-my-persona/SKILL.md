---
name: Sharpen My Persona
description: Update what you know about me — refresh the persona's Derived block from the library.
version: "1"
primitives: [persona]
artifacts: [persona.md]
---

# Sharpen My Persona

The user asks "update what you know about me", or you notice the library has
grown well past what the Derived block reflects.

## Steps

1. Run `openpod persona derive`. It reads the whole caught library and
   refreshes **only** the `## Derived from my library` block: episodes caught,
   most-followed shows, recurring themes. Explicit signal only — what the user
   chose to catch and keep. It never invents interests they didn't act on.
2. Report the path to `persona.md` and summarize what changed.
3. **Surface tension, don't resolve it silently.** If the derived signal
   contradicts the user's stated filters (they keep catching a topic marked
   "not interested"), mention it as an observation and let them decide whether
   to edit their own sections. Never edit the human sections yourself.

## Boundary

`derive` is idempotent and non-destructive above the marker. If you ever see
it touch the human sections, that's a bug to report, not a feature.
