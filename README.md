# OpenPod

**Turn long-form audio & video into personalized, cited, navigable briefings — entirely on your own machine.**

OpenPod is a local-first CLI and [MCP](https://modelcontextprotocol.io) server for people who have to keep up with long-form content for work. You (or your AI agent) point it at a podcast, an RSS feed, or a YouTube link; OpenPod returns a timestamped transcript, extracts the key ideas, builds a navigable table of contents, and lets you jump straight to the moment in the native player — or cut a local clip.

The important part: **nothing leaves your machine.** There is no OpenPod server, no account, no telemetry. The only "corpus" is your own library on disk, and it grows every session. Your AI agent brings the context and does the inference; OpenPod supplies extraction, structure, citations, deep-links, and local search.

> **Status:** Stage 1 — alpha. Local-pure, pull-only. Sync, always-on monitoring, and a hosted player are explicitly out of scope here.

---

## Why it's built this way

- **Local-pure.** No server, no central corpus, nothing of yours leaves your disk. That's both the privacy posture and the reason it's free to run.
- **Pull, not push.** You (or your agent) invoke everything. No background monitoring.
- **Artifacts, not telemetry.** OpenPod persists what you explicitly produce — transcripts, briefings, ideas, clips, notes — never behavioral tracking.
- **Navigate, don't re-host.** Deep-link cards link to the moment in YouTube/Spotify/the open podcast enclosure. OpenPod never republishes anyone's audio.
- **Agent-native.** The four primitives are exposed as MCP tools so Claude Code, Cowork, Codex, or any MCP client can drive the whole pipeline.

## Install

```bash
pip install openpod                 # core: RSS ingest, local search, deep-links, clip
pip install 'openpod[youtube]'      # + YouTube captions & audio (yt-dlp)
pip install 'openpod[asr]'          # + local Whisper transcription (faster-whisper)
pip install 'openpod[mcp]'          # + the MCP server
pip install 'openpod[all]'          # everything
```

`ffmpeg` on your `PATH` is required for `clip`.

## Quick start

```bash
# Catch an episode — writes transcript, ideas, and a briefing scaffold to .openpod/
openpod catch "https://example.com/podcast/feed.xml"
openpod catch "https://www.youtube.com/watch?v=VIDEO_ID"

# Already have a transcript? Skip the network entirely.
openpod catch "https://example.com/ep1" --kind podcast --transcript ./ep1.vtt

# Search across everything you've ever caught (keyword + local semantic)
openpod search "what did they say about raft consensus"

# Jump-to-moment timestamps with deep-links
openpod export-timestamps "test-pod/episode-one-consensus"

# Cut a local, sentence-snapped clip (needs ffmpeg) + a shareable deep-link card
openpod clip "test-pod/episode-one-consensus" 320 385

# Follow feeds locally and see what's new
openpod follow "https://example.com/feed.xml"
openpod digest

# A local, user-owned persona your agent reads to personalize briefings
openpod persona init
openpod persona derive
```

Everything lands in a plain, user-owned directory tree:

```
.openpod/
  persona.md              # who you are (evolving, local)
  follows.yaml            # subscribed RSS + YouTube channels
  library/
    <show>/<episode>/
      meta.json           # source + bookkeeping
      transcript.json     # timed cues
      briefing.md         # cited, personalized (authored by your agent)
      ideas.md            # key ideas, each with a deep-link
      clips/              # your saved media + share cards
      notes.md            # your annotations
  index/                  # local search index (SQLite FTS + embeddings)
```

Want it on two machines? Put the tree in git or Dropbox yourself. OpenPod won't sync it for you.

## The four primitives (CLI & MCP)

| Tool | Does | Writes |
|---|---|---|
| `catch <link>` | Ingest → transcribe → structure → brief | `transcript.json`, `ideas.md`, `briefing.md` |
| `clip <entry> <start> <end>` | Sentence-snapped cut + deep-link card | media file in `clips/` |
| `export_timestamps <entry>` | Timed segments + deep-links | JSON / Markdown |
| `search <query>` | Retrieve across the local library | ranked cues with deep-links |

Plus `follow` / `digest` and `persona` helpers.

## Use it from an AI agent (MCP)

Run the server:

```bash
openpod-mcp
```

Register it with an MCP client (e.g. Claude Code / Cowork). Example config:

```json
{
  "mcpServers": {
    "openpod": {
      "command": "openpod-mcp",
      "env": { "OPENPOD_HOME": "/path/to/your/workspace" }
    }
  }
}
```

The agent gets `catch`, `search`, `export_timestamps`, `clip`, `get_briefing`, `persona`, `follow`, and `digest`. A typical flow: the agent `catch`es a link, reads the ideas + TOC + transcript via `get_briefing`, reads your `persona`, and writes the personalized, cited briefing back into `briefing.md`.

## Use it as a library

```python
from openpod import catch, search

result = catch("https://example.com/ep1", transcript_path="ep1.vtt", kind="podcast")
print(result.entry_id, len(result.transcript), result.ideas)

for hit in search("consensus algorithms"):
    print(hit.show, hit.start, hit.deeplink)
```

## How transcription is chosen

1. A publisher-provided **timed transcript** (`podcast:transcript`, YouTube captions) is used when available — free, fast, good enough for navigation.
2. Otherwise the audio is downloaded and transcribed **locally** with Whisper (`asr` extra). Nothing is uploaded.

Caption-cue timing (±2–4s) is fine for jumping to a moment; `clip` snaps cut boundaries to cue edges so clips land on sentence boundaries.

## Development

```bash
git clone https://github.com/openpod/openpod
cd openpod
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest
```

The test suite runs fully offline against fixtures — no network, no heavy models.

## License

[AGPL-3.0-or-later](LICENSE). If you run a modified version as a network service, the AGPL requires you to offer users your source. This keeps the engine open and stops it being resold as a closed service.

Long-form is a lot. OpenPod gives you the five minutes that matter — and a link straight to them.
