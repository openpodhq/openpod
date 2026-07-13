---
name: Set Up My Persona
description: Learn who I am — guess from the workspace, let the user confirm, seed the persona.md that personalizes everything.
version: "3"
primitives: [persona, persona_scan, persona_split]
artifacts: [persona.md, ~/.openpod/persona.md]
---

# Set Up My Persona

The user wants OpenPod's output tuned to them ("learn who I am", or they bit
on the Catch Me Up nudge). The governing principle: **we guess, the user
chooses.** Never ask a cold open question the evidence could have answered —
the user's effort budget is picking from options, not writing essays.

## Step 0 — gather evidence before asking anything

1. Call `persona_scan()`. It returns the workspace's own signal: project
   folders ordered by recency, document titles, `CLAUDE.md` headings, themes
   from the caught library, and the follows list (including anything OPML
   import already brought in). If the user offers another folder ("my work
   stuff is in ~/Work"), pass it as `extra_roots` — never scan paths they
   didn't point at.
2. Mine **your own** context and memory too — the global CLAUDE.md, your
   memory files, this conversation. That's your asset; combine it with the
   scan. OpenPod never reads it.

## Step 1 — interview as guess-and-confirm (multi-select, ~2 minutes)

Run `openpod persona init` first (idempotent). Then ask the five questions
(`persona interview` lists them) — but every one is presented as
**multi-select options built from the evidence**, plus a free-text option for
elaboration. Rules:

- **Multi-select always.** Projects, topics, extraction goals are not
  mutually exclusive; single-choice with an "Other" box is a form, not a
  conversation.
- **Options must be specific and evidenced.** "OpenPod — OSS strategy (saw
  `Docs/OpenPod_OSS_Marketing_Strategy.md`)" beats "software projects". For
  each amplify-topic, suggest the *angle* that would matter in new content
  ("OSS strategy → licensing choices, launch playbooks, monetization gates").
- **Don't ask what the disk already answered.** Trusted shows come from
  follows/imports, not a question. If the library shows recurring themes,
  present them as pre-checked guesses to confirm or reject.
- **Filters get guesses too** — offer plausible noise for *this* user
  (topics adjacent to their field they likely don't want), not a blank box.
- **Filters are topics, never sources.** Don't ask which shows to trust or
  avoid — follows/imports already say what they subscribe to, and unfamiliar
  sources are the discovery pool, not noise. The digest deliberately
  surfaces interest-matching episodes from shows they've never sampled.

## Step 2 — write to the right layer, then derive

The persona is **two files**: global (`~/.openpod/persona.md` — who the
user is everywhere) and workspace (`.openpod/persona.md` — what this
folder is for). Route each confirmed answer by kind:

- **Global**: Role, Language & style (preferred language belongs here *and*
  in `locale.preferred_language`), Standing interests/filters, What I want
  from long-form. `openpod persona init --global` is idempotent.
- **Workspace**: Current projects, Interests (amplify) with the chosen
  angles, Not interested (filter). `openpod persona init` is idempotent.

Write in the user's own words where they elaborated, in the confirmed
option text where they picked. Never write a guess the user didn't
confirm. Then run `openpod persona derive` (workspace-local by nature) and
report both paths — theirs to hand-edit, never uploaded.

## Splitting an older single-file persona

If `persona()` returns a `split_proposal`, this workspace's persona
predates the global layer. Offer the split as **one multiple-choice
confirmation**: list the `proposed_global` sections (pre-checked) and any
`unclassified` ones (unchecked), let the user toggle and confirm, then
call `persona_split(to_global=[...])` with exactly their selection. Zero
prose required from the user; never split without the confirmation; never
offer machine-owned sections.

## Boundary (hard contract)

Confirmed answers go in the human sections only because the user approved
them — you are transcribing choices, not inventing interests. The
`## Derived from my library` and `## Imported interests (opt-in)` blocks stay
machine-owned; never hand-edit those, and never let a derive touch the human
sections.
