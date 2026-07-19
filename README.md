<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/openpodhq/openpod/main/assets/brand/wordmark-blue-dark-636.png">
    <img src="https://raw.githubusercontent.com/openpodhq/openpod/main/assets/brand/wordmark-blue-636.png" alt="OpenPod" width="212">
  </picture>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/openpodhq/openpod/main/assets/demo.gif" alt="An AI agent pulls the minutes that matter from hundreds of hours across the shows you follow — every hit timestamped to verify" width="720">
</p>

# OpenPod

**Pull the ten minutes that matter — to you.**

[![PyPI](https://img.shields.io/pypi/v/openpod)](https://pypi.org/project/openpod/) [![Python](https://img.shields.io/pypi/pyversions/openpod)](https://pypi.org/project/openpod/) [![CI](https://github.com/openpodhq/openpod/actions/workflows/ci.yml/badge.svg)](https://github.com/openpodhq/openpod/actions) [![License](https://img.shields.io/badge/license-MIT-black)](LICENSE) [![Telemetry](https://img.shields.io/badge/telemetry-0-black)](#nothing-leaves-your-machine) [![MCP](https://img.shields.io/badge/MCP-server-black)](https://modelcontextprotocol.io)

Hundreds of hours pile up across the shows you follow. OpenPod pulls out the minutes you're chasing — cited, timestamped to verify, extracted on your own machine — as the starting point your AI agent works from.

OpenPod is a local-first agent toolkit — primitives plus packaged skills — that your agent drives over MCP or the CLI. It connects to nothing external; the only corpus is your library on disk.

```bash
pip install openpod
openpod catch "https://example.com/podcast/feed.xml"
```

**Catch your first episode** — one command, about a minute. You get a timestamped transcript, the key ideas, and jump-to-moment links, all as plain files in a folder you own.

`0 servers · 0 accounts · 0 telemetry · $0 to run · test suite runs fully offline`

> **Status:** Stage 1 alpha. Local-pure, pull-only. It does what this page shows — nothing more yet.

---

## The problem

You subscribe to twelve shows. You're forty episodes behind, every one is three hours long, and the one segment you actually needed last week — you can't find it, because nobody remembers a timestamp. So the backlog grows, the guilt compounds, and "I'll listen on the weekend" becomes a lie you tell yourself on Mondays.

Cloud summarizers answer this by asking you to upload what you listen to — and to trust their summary with no way to check it. OpenPod doesn't summarize your listening on someone else's computer. It gives **your** agent the ability to listen, cite, and link — locally.

## What you get

Point OpenPod (or your agent) at an RSS feed or a YouTube link. In return:

- **A briefing you can verify.** Every claim carries a timestamp and a deep link. Don't trust the summary? Click and hear the source in the native player. OpenPod never re-hosts anyone's audio — it navigates you to the moment.
- **A library that compounds.** Everything you catch lands in `.openpod/` as plain files — transcripts, briefings, ideas, notes, clips. Searchable across everything you've ever caught, keyword and semantic, all local.
- **Personalized by your own agent.** OpenPod reads your local `persona.md`, so your AI foregrounds the decisions, tools, and numbers *you* care about and drops the rest — the same episode briefs differently for you than for the next person, and no cloud model ever sees who you are.

## Nothing leaves your machine

There is no OpenPod server, no account, no telemetry — there is nothing to opt out of, because nothing phones home. The only corpus is your own library on disk. Publisher transcripts and captions are used when they exist; otherwise audio is transcribed locally with Whisper. Your agent brings the intelligence; OpenPod supplies extraction, structure, citations, deep links, and local search. That's the whole trade — and it's why running it costs nothing.

## Your agent can drive all of it

OpenPod's toolkit is exposed as an [MCP](https://modelcontextprotocol.io) server, so Claude Code, Cowork, Codex — any MCP client — can catch, search, clip, and brief by name:

```bash
openpod-mcp
```

```json
{
  "mcpServers": {
    "openpod": { "command": "openpod-mcp", "env": { "OPENPOD_HOME": "/path/to/your/workspace" } }
  }
}
```

Say *"catch me up on this episode"* and the agent runs `catch`, reads your local persona file, and writes a cited briefing back into your library — then hands you the path. Mid-conversation, no dashboard, no copy-paste.

## The four primitives

| Tool | Does | Writes |
|---|---|---|
| `catch <link>` | Ingest → transcribe → structure → brief | `transcript.json`, `transcript.md`, `ideas.md`, `briefing.md` |
| `search <query>` | Retrieve across your local library | ranked cues with deep links |
| `export_timestamps <entry>` | Navigable TOC with deep links | JSON / Markdown |
| `clip <entry> <start> <end>` | Sentence-snapped local cut + shareable deep-link card | media in `clips/`, card |

Every mutating command prints the path it wrote — the output of an action is the address of the artifact it produced. Every read command takes `--json`, shaped identically to the matching MCP tool.

## Skills — the features, by name

Primitives are the API; **skills are the product.** Each is a versioned instruction bundle (`openpod skills` lists them; the MCP server exposes them as prompts) that tells an agent how to compose the primitives and report back:

| Skill | You ask | It writes |
|---|---|---|
| **Catch Me Up** | "Brief me on this episode" | `briefing.md`, `ideas.md` |
| **Find the Moment** | "Where did they talk about X?" | deep-linked hits |
| **What's New** | "What dropped in my feeds?" | digest |
| **Cut the Clip** | "Pull the shareable minute" | media + share card |
| **Chapter It** | "Give me a navigable TOC" | timestamps (md/json) |
| **Follow This** | "Keep me on this show" | `follows.yaml` entry |
| **Set Up My Persona** | "Learn who I am" | `persona.md` — yours, local |
| **Sharpen My Persona** | "Update what you know about me" | persona Derived block |
| **Bring In My World** | "Import my subscriptions" | `imports/`, `follows.yaml` |

## More things it does

```bash
# Already have a transcript? Skip the network entirely.
openpod catch "https://example.com/ep1" --kind podcast --transcript ./ep1.vtt

# Search everything you've ever caught (keyword + local semantic)
openpod search "what did they say about raft consensus"

# Follow feeds locally; see what's new on your schedule — nothing polls in the background
openpod follow "https://example.com/feed.xml"
openpod digest

# Cut a local, sentence-snapped clip (needs ffmpeg) + a shareable deep-link card
openpod clip "test-pod/episode-one-consensus" 320 385

# Import subscriptions from a file you export (OPML) — point-in-time, opt-in
openpod import ~/Downloads/overcast.opml

# Annotate; notes feed future personalization
openpod note "test-pod/episode-one-consensus" "the Raft segment matters for our design"
```

Everything lands in a plain, user-owned tree:

```
.openpod/
  persona.md              # who you are (evolving, local)
  follows.yaml            # subscribed RSS + YouTube channels
  library/<show>/<episode>/
    transcript.json  transcript.md  briefing.md  ideas.md  summary.md  notes.md  clips/
  index/                  # local search index (SQLite FTS + embeddings)
```

`transcript.json` is the machine's copy. `transcript.md` is the one you read — the same words
reflowed into paragraphs, chaptered, with a `▸ m:ss` link per paragraph that opens the player
at that moment. Open it in any editor that previews Markdown; the chapter headings become the
outline pane.

Want it on two machines? Put the tree in git or Dropbox yourself. OpenPod won't sync it for you.

## Install options

```bash
pip install openpod                 # core: RSS ingest, local search, deep links, clip
pip install 'openpod[youtube]'      # + YouTube captions & audio
pip install 'openpod[asr]'          # + local Whisper transcription
pip install 'openpod[mcp]'          # + the MCP server
pip install 'openpod[all]'          # everything
```

`ffmpeg` on your `PATH` is required for `clip`.

**Burned-in captions and the speaker name-plate additionally need an ffmpeg
built with `libass` (the `subtitles` filter) and `drawtext`.** Without them
`clip` still runs, but it skips the styled burn-in and writes a plain `.srt`
sidecar instead — which players render in their own generic style, not the
OpenPod caption look. Many stock builds omit these filters:

- **macOS (Homebrew):** the default `brew install ffmpeg` is now a *minimal*
  build with no libass/drawtext. Install the full formula — `brew install
  ffmpeg-full` — then make it your `ffmpeg`: it's keg-only, so either prepend
  it to `PATH` with `export PATH="$(brew --prefix ffmpeg-full)/bin:$PATH"` (in
  your shell profile), or force-link it with `brew link --overwrite --force
  ffmpeg-full`.
- **Debian/Ubuntu:** `apt install ffmpeg` already includes libass and
  drawtext — nothing extra needed.
- **conda / other builds:** some (notably conda-forge) ship without libass;
  if so, use a static build or your system ffmpeg for clipping.

Run `openpod doctor` to see exactly what your ffmpeg can do — `subtitles` is
libass (caption burn-in), `drawtext` is the label plate.

## Use it as a library

```python
from openpod import catch, search

result = catch("https://example.com/ep1", transcript_path="ep1.vtt", kind="podcast")
print(result.entry_id, len(result.transcript), result.ideas)

for hit in search("consensus algorithms"):
    print(hit.show, hit.start, hit.deeplink)
```

## Trust & posture FAQ

**Does OpenPod download or re-host other people's content?**
It navigates, it doesn't republish. Deep links open the moment in YouTube, Spotify, or the open podcast enclosure. Podcast RSS — content published for open distribution — is the primary surface; YouTube support is an optional module that uses official captions, or local transcription of audio you can already access. OpenPod never touches DRM, paywalls, or age gates.

**What data do you collect about me?**
None. There is no endpoint to send it to.

**How is transcription chosen?**
Publisher-provided timed transcripts and captions first — free, fast, good for navigation (±2–4s cue timing). Otherwise local Whisper (`asr` extra). Nothing is uploaded either way. `clip` snaps cuts to cue boundaries so clips land on sentences.

**Why MIT?**
[MIT](LICENSE): maximally permissive and frictionless — install it at work without tripping a license policy, embed it, build on it, no obligations. OpenPod isn't protected by its license; it's protected by being local-pure and simple. No copyleft, no lock-in.

**Can I hack on it without heavy models or network?**
Yes — the test suite runs fully offline against fixtures.

Built by Yoav — building OpenPod in public.

## Development

```bash
git clone https://github.com/openpodhq/openpod
cd openpod
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest
```

Contributions that package or connect OpenPod travel furthest: Homebrew formula, Docker image, MCP client recipes, note-app bridges. Open an issue first; response is fast.

---

**Long-form is a lot. OpenPod pulls the ten minutes that matter — to you — with a link straight to every one.**

```bash
pip install openpod
```

If it saved you an hour this week, [star the repo](../../stargazers) — stars are how the next person finds it.
