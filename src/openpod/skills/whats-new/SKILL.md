---
name: What's New
description: What dropped in my feeds? — a persona-ordered digest that also surfaces what's worth trying outside the usual rotation.
version: "2"
primitives: [digest, persona, catch, search]
artifacts: []
---

# What's New

The user asks what's new across their follows. This is the retention loop —
and the discovery surface. The asymmetry that governs it: **a false positive
costs the user a two-minute briefing; a missed gem costs the reason they use
the product.** People already know what they trust and will get to it on
their own; the value you add is finding the thing they'd have passed on.

## Steps

1. Call `digest()` — it polls the followed feeds locally and returns fresh
   episodes, each marked `in_rotation` (has the user ever caught this show?).
2. Read `persona()` and rank by **content match to their interests — never by
   source familiarity.** Present two groups:
   - **Your rotation** — new episodes from shows they actually catch.
   - **New to you** — episodes from follows they've never sampled (the OPML
     import usually brings dozens) whose *topic* matches what they amplify.
     Lead with why it matches ("this hits your orchestration-frameworks
     interest"), not with the show's name recognition.
3. **Filters apply to topics, not sources.** "Not interested: crypto" hides
   crypto episodes; it never hides an unfamiliar show. Unfamiliarity is the
   opportunity, not noise.
4. **Offer the cheap trial, not the commitment.** For anything new-to-you:
   catch it and hand them the briefing, or jump straight to the one beat that
   matches their interest (anchor ladder). "Try the 3 minutes on X before
   deciding about the 2 hours" is the pitch — never "subscribe to this".
5. If the user wants discovery beyond their follows, that's *your* reach, not
   OpenPod's: search the open web through your own tools for episodes on
   their amplify topics, then `catch` the candidate. OpenPod stays local; you
   bring the horizon.
6. If there are no follows yet, point at "Follow This" or "Bring In My World"
   to seed them.
