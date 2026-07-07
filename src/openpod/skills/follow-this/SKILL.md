---
name: Follow This
description: Keep me on this show — add a podcast or YouTube channel to the local follows list.
version: "1"
primitives: [follow]
artifacts: [follows.yaml]
---

# Follow This

The user wants to stay current on a show ("keep me on this", "follow this
channel").

## Steps

1. Call `follow(url)` with the RSS feed or YouTube channel URL (channel URLs
   are normalized to their RSS feed automatically). Pass a `title` when you
   know the human name of the show.
2. Report that the follow landed in `follows.yaml` — a local list the user
   owns and can hand-edit; nothing syncs anywhere.
3. Mention that "What's New" now covers this show, and that following declares
   the topic universe future personalization draws from.
4. To stop, `openpod unfollow <url>` — or the user just deletes the line in
   `follows.yaml`.
