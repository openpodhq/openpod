---
name: Find the Moment
description: Where did they talk about X? — search the library and return deep-linked moments.
version: "1"
primitives: [search, export_timestamps, persona]
artifacts: []
---

# Find the Moment

The user wants the exact moment something was said ("where did they talk about
X?"). Read-only: this skill writes nothing.

## Steps

1. Call `search(query)` across the local library. Each hit carries the
   `entry_id`, timestamp, and a deep-link to the moment in the native player.
2. **Rank for this user.** Read `persona()` and use its interests as a
   tie-breaker when several hits are plausible — surface the ones matching
   what they amplify.
3. Present hits as clickable deep-links with a one-line quote each, and name
   the episode (`entry_id`) so they can open its artifacts. **Offer the whole
   anchor ladder, labeled** — links are cheap, so don't pick for the user:
   - *chapter* (`chapter_deeplink`) — the creator's own chapter ("take me to
     the topic");
   - *beat* (`segment_deeplink`) — where the idea starts being articulated;
     usually the best default;
   - *said at* (`deeplink`) — the exact sentence, for when they want the
     quote itself.
   One short line explaining what each lands on beats a single unexplained
   link. Rungs that are missing or identical are simply omitted.
4. If they want the surrounding structure, call
   `export_timestamps(entry_id)` for the episode's navigable TOC.
5. If nothing matches, say so and name the fix: maybe the episode isn't caught
   yet (`catch` it), or the index is stale (`openpod reindex`).

## Cite, always

Never paraphrase a moment without its deep-link — the link is the credibility
guarantee.
