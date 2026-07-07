---
name: Bring In My World
description: Import my subscriptions & saves — seed follows and persona from OPML and other explicit signal.
version: "1"
primitives: [import_opml, persona, follow]
artifacts: [imports/**, follows.yaml, persona.md]
---

# Bring In My World

The user has already declared their interests elsewhere — podcast
subscriptions, saved links. Import that explicit signal so digests and
briefings work from day one. Strictly opt-in: nothing is imported unless the
user asks.

## Steps

1. **OPML first.** Ask the user to export subscriptions from their podcast app
   (Overcast: Settings → Export OPML; Pocket Casts / Apple Podcasts similar)
   and give you the file. Call `import_opml(path)`.
2. **Report what happened.** The result lists every path touched: the raw
   export staged verbatim in `imports/`, follows merged into `follows.yaml`
   (each tagged `source: opml:…` so the user can see and trim what the import
   added), and the refreshed `## Imported interests (opt-in)` persona block.
   Say how many follows were added and how many were already there.
3. **Cloud sources go through you, not OpenPod.** Spotify shows, YouTube
   subscriptions, Reddit saves, X bookmarks are cloud-API-only. If you have a
   connector for one, pull the list through *your* auth, write it to a plain
   file, and import that. OpenPod holds no OAuth tokens and calls no cloud
   APIs.
4. **Propose, don't promote.** If the import reveals a strong interest, you
   may *propose* adding it to the user's own "Interests (amplify)" section —
   but the user accepts it in their own words. The machine block never edits
   the human sections.
5. **Optionally re-derive.** Run `openpod persona derive` so the Derived block
   reflects the new state, and offer "What's New" across the fresh follows.

## Snapshot, not sync

Every import is a point-in-time snapshot the user re-runs when they want a
refresh. Never poll or schedule imports.
