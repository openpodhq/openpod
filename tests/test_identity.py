"""Cross-codebase identity parity with the player's schemas/src/identity.ts.

These vectors come straight from the TS source. If any drift, the OpenPod push
and the player's records would key different episodes on different hashes and
sync would silently split every episode into two. Keep them byte-for-byte.
"""

from openpod.identity import episode_key, normalize_feed_url


def test_normalize_passthrough():
    assert normalize_feed_url("https://example.com/feed") == "https://example.com/feed"


def test_normalize_lowercases_and_strips_default_port_and_utm_and_slash():
    # HTTPS + :443 (default) dropped, host/scheme lowercased, trailing slash
    # removed, utm_source dropped, b=2 kept in original order.
    assert (
        normalize_feed_url("HTTPS://Example.COM:443/feed/?utm_source=x&b=2")
        == "https://example.com/feed?b=2"
    )


def test_normalize_keeps_non_default_port():
    assert (
        normalize_feed_url("https://example.com:8443/feed")
        == "https://example.com:8443/feed"
    )


def test_vector_guid():
    key, conf = episode_key("https://example.com/feed", guid="ep-1")
    assert key == "23966992069e517dbbde9c4bdf9c02c240edc13a1d09e0ecbed3b9d0e7e32754"
    assert conf == "guid"


def test_vector_guid_with_normalization():
    key, conf = episode_key("HTTPS://Example.COM:443/feed/?utm_source=x&b=2", guid="ep-1")
    assert key == "9de6c1680ad67c9c31bedb9e244701e59c61bee612f5d1a87969d6e718566984"
    assert conf == "guid"


def test_vector_enclosure():
    key, conf = episode_key(
        "https://example.com/feed", audio_url="https://cdn.example.com/a.mp3"
    )
    assert key == "992d5eabf91fc54c62812db8bf826cf80da1b089601d4ab837466236c78c517e"
    assert conf == "enclosure"


def test_vector_derived():
    key, conf = episode_key(
        "https://example.com/feed", title="Ep 1", published="2026-01-01"
    )
    assert key == "2faaa4cc63bee9a593918d0ab7bf83ef52b2e0d99422f19f1edc996acf6927db"
    assert conf == "derived"


def test_ladder_prefers_guid_over_enclosure():
    with_guid, conf = episode_key(
        "https://example.com/feed",
        guid="ep-1",
        audio_url="https://cdn.example.com/a.mp3",
    )
    guid_only, _ = episode_key("https://example.com/feed", guid="ep-1")
    assert conf == "guid"
    assert with_guid == guid_only


def test_empty_guid_falls_through_to_enclosure():
    key, conf = episode_key(
        "https://example.com/feed",
        guid="   ",
        audio_url="https://cdn.example.com/a.mp3",
    )
    assert conf == "enclosure"


# --- WHATWG parity: IDN hosts + non-ASCII paths (must match `new URL(...)`) ---


def test_normalize_idn_host_punycode():
    # new URL('https://münchen.example.com/feed').hostname => xn--mnchen-3ya…
    assert (
        normalize_feed_url("https://münchen.example.com/feed")
        == "https://xn--mnchen-3ya.example.com/feed"
    )
    assert (
        normalize_feed_url("https://café-podcast.fr/feed.xml")
        == "https://xn--caf-podcast-dbb.fr/feed.xml"
    )
    assert normalize_feed_url("https://例え.jp/feed") == "https://xn--r8jz45g.jp/feed"


def test_normalize_percent_encodes_non_ascii_path():
    assert (
        normalize_feed_url("https://example.com/podcasts/entrevías/feed.xml")
        == "https://example.com/podcasts/entrev%C3%ADas/feed.xml"
    )
    assert (
        normalize_feed_url("https://example.com/日本/feed")
        == "https://example.com/%E6%97%A5%E6%9C%AC/feed"
    )
    assert (
        normalize_feed_url("https://example.com/path with space/x")
        == "https://example.com/path%20with%20space/x"
    )


def test_idn_episode_key_matches_across_the_bridge():
    # A bookmark/segment on an IDN feed must resolve to ONE episode_key so the
    # player overlay and OSS write-back correlate. The key is stable and derived
    # from the punycode host (regression for the identity-parity finding).
    k1, _ = episode_key("https://café-podcast.fr/feed.xml", guid="ep-9")
    k2, _ = episode_key("https://xn--caf-podcast-dbb.fr/feed.xml", guid="ep-9")
    assert k1 == k2  # raw-unicode and punycode inputs converge
