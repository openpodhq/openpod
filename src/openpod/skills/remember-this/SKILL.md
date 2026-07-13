---
name: Remember This
description: Distill an episode into summary.md — the durable record future sessions load as context, personalized by what actually happened in this one.
version: "1"
primitives: [save_summary, recall_summaries, get_briefing, search, persona]
artifacts: [summary.md]
---

# Remember This

The user finished engaging with an episode — listening, discussing,
clipping, arguing with it — and wants a durable record. `summary.md` is
that record: local now, synced to any device later, and **loaded by future
sessions as context**. Write it so a session six months from now picks up
the thread instead of starting over.

## What OpenPod owns vs. what you own

OpenPod owns the file's location and identity frontmatter (entry, episode
key, timestamps) — that's what makes it recallable and syncable. Everything
else — structure, language, length, what counts as relevant — is yours and
the user's. The guidance below is what usually works, not a template to
obey.

## Guidance (defaults, not mandates)

- **Personalize from the session, not just the transcript.** The transcript
  says what was said; the session says what *mattered*. What did the user
  ask about, push back on, quote, clip, or decide to act on? That's the
  spine. An episode summary nobody discussed is just a worse briefing.
- **Exclude what didn't matter to this user.** Skipping the ad reads is
  obvious; also skip whole segments the user didn't care about. Recording
  that you skipped them ("second half on real estate — not relevant") costs
  one line and saves the next session re-checking.
- **Cite moments.** Claims worth remembering deserve the deep-links you
  already have from catch/search — a future session (or the user on another
  device) jumps straight to the source instead of trusting the summary.
- **Budget it.** This file gets loaded into context, possibly alongside
  many others. A tight page beats an exhaustive five. If it's growing,
  distill — don't accumulate.
- **Sessions append, distillations replace.** A quick follow-up discussion:
  `append=true` with a dated section. A genuinely revised understanding:
  rewrite the whole body. Your call; the frontmatter tracks revisions
  either way.
- **The user's language.** If the session ran in Hebrew, the record is more
  useful in Hebrew. `persona()` and `locale.preferred_language` are hints,
  the session itself is the answer.

## Boundaries

- `notes.md` is the user's own voice — quote it if useful, never migrate or
  rewrite it into the summary.
- The summary records the *user's* engagement; don't inject your own
  editorial takes as if they were theirs — attribute ("we concluded" vs
  "the host claims").

## Recall (the other half of the feature)

At the start of a podcast-related session, call `recall_summaries` — with a
query if the topic is known. That's how this artifact pays for itself: the
conversation continues instead of restarting.
