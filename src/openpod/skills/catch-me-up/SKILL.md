---
name: Catch Me Up
description: Brief me on this episode — a personalized, cited briefing for a podcast or YouTube link.
version: "1"
primitives: [catch, get_briefing, persona]
artifacts: [briefing.md, ideas.md]
---

# Catch Me Up

The user hands you a link to a long-form episode ("catch me up on this",
"brief me on this episode"). Turn it into a briefing written *for them*.

## Steps

1. **Ingest.** Call `catch(link)`. OpenPod transcribes, extracts key ideas and
   a navigable TOC, and scaffolds `briefing.md`. Note the `entry_id` and
   `artifacts_dir` from the result.
2. **Load context.** Call `get_briefing(entry_id)` for the ideas, TOC, and full
   transcript, and `persona()` for who you're writing for. Also read the
   entry's `notes.md` if it exists — it's the user's own signal.
3. **Author the briefing.** Rewrite the scaffold's Triage and Summary sections:
   - Foreground what the persona says to **amplify**; compress or drop what it
     says to **filter**.
   - Frame around "What I want from long-form" (decisions, named tools,
     numbers, the five minutes that change their mind).
   - **Every claim carries a deep-link** to the source moment. A sentence that
     isn't one click from the moment that justifies it isn't done.
   - **Cite with the anchor ladder.** Ideas carry up to three labeled
     anchors: `chapter_*` (the creator's chapter), `segment_*` (the beat
     where the idea starts being articulated — the best default), and
     `deeplink` (the exact sentence). Default claims to the beat; where a
     reader might want either the topic context or the verbatim quote, give
     the extra rung with its label. Never leave only a mid-sentence link.
4. **Write it back.** Save your prose into `briefing.md` at the path from step
   1 — never leave the briefing only in chat — and tell the user the path you
   wrote.
5. **The nudge.** If the persona is empty or missing, end the briefing with one
   line: *"I wrote this for a general reader. Tell me who you are and I'll
   write the next one for you — 2 minutes."* (That's the "Set Up My Persona"
   skill.) Never block the briefing on setup.

## Quality bar

Two different users catching the same episode should get visibly different
briefings, and every sentence should be one click from the moment that
justifies it. If either isn't true, the skill isn't done.

## Stays local

No egress beyond fetching the source the user pointed at. You bring the
inference; OpenPod supplies structure.
