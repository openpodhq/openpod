import textwrap
import wave
from pathlib import Path

import pytest

from openpod.config import Workspace

SAMPLE_VTT = textwrap.dedent(
    """\
    WEBVTT

    00:00:00.000 --> 00:00:05.000
    Welcome to the show about distributed systems and databases.

    00:00:05.000 --> 00:00:11.000
    Today we talk about consensus algorithms like Raft and Paxos.

    00:00:11.000 --> 00:00:18.000
    Raft is easier to understand than Paxos for most engineers.

    00:00:18.000 --> 00:00:25.000
    We also cover vector databases and embeddings for search.

    00:01:00.000 --> 00:01:06.000
    Finally, a note on latency budgets and tail latency in practice.
    """
)

SAMPLE_RSS = textwrap.dedent(
    """\
    <?xml version="1.0"?>
    <rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0">
      <channel>
        <title>Test Pod</title>
        <item>
          <title>Episode One: Consensus</title>
          <guid>ep-001</guid>
          <pubDate>Tue, 01 Jul 2026 10:00:00 GMT</pubDate>
          <link>https://example.com/ep1</link>
          <enclosure url="https://example.com/ep1.mp3" type="audio/mpeg"/>
        </item>
      </channel>
    </rss>
    """
)


@pytest.fixture
def workspace(tmp_path) -> Workspace:
    return Workspace(tmp_path).ensure()


@pytest.fixture
def vtt_file(tmp_path) -> Path:
    p = tmp_path / "sample.vtt"
    p.write_text(SAMPLE_VTT, encoding="utf-8")
    return p


@pytest.fixture
def tiny_wav_file(tmp_path) -> Path:
    """A few seconds of silence — enough for `clip()` to cut from, without a
    real download or network fetch."""
    p = tmp_path / "sample.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 8000 * 10)  # 10s of silence
    return p
