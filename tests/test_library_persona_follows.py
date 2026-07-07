from openpod.catch import catch
from openpod.follows import Follows, _normalize
from openpod.ingest.rss import parse_feed
from openpod.persona import Persona
from openpod.models import slugify
from tests.conftest import SAMPLE_RSS


def test_slugify():
    assert slugify("The Tim Ferriss Show!") == "the-tim-ferriss-show"
    assert slugify("") == "item"
    assert slugify("   ") == "item"


def test_rss_parse_stdlib_or_feedparser():
    feed = parse_feed(SAMPLE_RSS)
    assert feed.title == "Test Pod"
    assert len(feed.items) == 1
    item = feed.items[0]
    assert item.enclosure_url == "https://example.com/ep1.mp3"
    assert item.guid == "ep-001"


def test_follows_add_list_remove(workspace):
    f = Follows(workspace)
    f.add("https://example.com/feed.xml", title="Test Pod")
    assert len(f.list()) == 1
    assert f.list()[0].kind == "podcast"
    assert f.remove("https://example.com/feed.xml")
    assert f.list() == []


def test_follow_youtube_channel_normalizes_to_rss():
    url = _normalize("https://www.youtube.com/channel/UC12345")
    assert url == "https://www.youtube.com/feeds/videos.xml?channel_id=UC12345"


def test_persona_init_and_derive(workspace, vtt_file):
    persona = Persona(workspace)
    path = persona.init()
    assert path.endswith("persona.md")
    assert persona.exists()
    # derive over a library with one caught episode
    catch("https://example.com/ep1", workspace=workspace, kind="podcast",
          transcript_path=str(vtt_file))
    block = persona.derive()
    assert "Episodes caught" in block
    assert "Episodes caught" in persona.read()


def test_digest_marks_unlistened_follows_as_new_to_you(workspace, monkeypatch):
    """The discovery asymmetry: follows never caught are the pool worth
    sampling, so digest items carry in_rotation for the agent to split on."""
    from openpod.ingest import rss
    from openpod.library import Library
    from openpod.models import SourceRef

    # one show actually in the rotation (caught before)
    entry = Library(workspace).entry("Test Pod", "ep0")
    entry.write_meta(SourceRef(kind="podcast", show="Test Pod"))

    monkeypatch.setattr(rss, "load_feed",
                        lambda url: rss.parse_feed(SAMPLE_RSS))
    f = Follows(workspace)
    f.add("https://example.com/a.xml", title="Test Pod")
    f.add("https://example.com/b.xml", title="Other Show")

    rotation = {i.show: i.in_rotation for i in f.poll()}
    assert rotation == {"Test Pod": True, "Other Show": False}
