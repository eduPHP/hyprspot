import json
from pathlib import Path

from hyprspot.model import Playlist, Track
from hyprspot.webapi import parse_playlists, parse_search, retry_delay, search_url

FX = Path(__file__).parent / "fixtures"


def test_parse_playlists():
    body = json.loads((FX / "me_playlists.json").read_text())
    items, nxt = parse_playlists(body)
    assert len(items) == len(body["items"])
    assert all(isinstance(p, Playlist) and p.uri.startswith("spotify:playlist:") for p in items)
    assert items[0].name == body["items"][0]["name"]
    assert nxt is None


def test_parse_playlists_next_and_missing_images():
    items, nxt = parse_playlists({"items": [{"uri": "spotify:playlist:a", "name": "A", "owner": {}, "images": None}],
                                  "next": "https://api.spotify.com/v1/me/playlists?offset=50"})
    assert items[0].image_url is None and items[0].owner == ""
    assert nxt.endswith("offset=50")


def test_parse_search():
    tracks = parse_search(json.loads((FX / "search_tracks.json").read_text()))
    assert len(tracks) == 10
    assert all(isinstance(t, Track) and t.uri.startswith("spotify:track:") for t in tracks)
    assert tracks[0].artist == "Elis Regina, Antônio Carlos Jobim"
    assert tracks[0].duration_ms > 0 and tracks[0].art_url


def test_parse_search_empty():
    assert parse_search({"tracks": {"items": []}}) == []
    assert parse_search({}) == []


def test_search_url_encodes():
    u = search_url("água de março", 10)
    assert "q=%C3%A1gua+de+mar%C3%A7o" in u
    assert "limit=10" in u and "offset=10" in u


def test_search_url_asks_tracks_and_playlists():
    assert "type=track%2Cplaylist" in search_url("rock", 0)


def test_parse_search_playlists():
    from hyprspot.webapi import parse_search_playlists
    body = {"playlists": {"items": [
        None,  # a API às vezes manda buracos na lista
        {"uri": "spotify:playlist:a", "name": "Rock Classics", "owner": {"display_name": "Spotify"},
         "images": [{"url": "http://x/a.jpg", "width": 300}]},
    ]}}
    pls = parse_search_playlists(body)
    assert [(p.uri, p.name, p.owner, p.image_url) for p in pls] == [
        ("spotify:playlist:a", "Rock Classics", "Spotify", "http://x/a.jpg")]
    assert parse_search_playlists({}) == []


def test_retry_delay_quota():
    assert retry_delay(429, {}, {"error": {"reason": "QUOTA_EXCEEDED"}}) == 60


def test_retry_delay_retry_after():
    assert retry_delay(429, {"Retry-After": "3"}, None) == 3


def test_retry_delay_server_error():
    assert retry_delay(503, {}, None) == 2


def test_retry_delay_other():
    assert retry_delay(404, {}, None) is None
    assert retry_delay(200, {}, None) is None


def test_liked_playlist_uses_collection_context():
    from hyprspot.webapi import liked_playlist
    p = liked_playlist("31gvq")
    assert p.uri == "spotify:user:31gvq:collection"
    assert p.name == "Músicas Curtidas"


def test_parse_track_single():
    from hyprspot.webapi import parse_track
    body = json.loads((FX / "search_tracks.json").read_text())["tracks"]["items"][0]
    t = parse_track(body)
    assert t.title == "Águas De Março" and t.art_url and t.duration_ms > 0
    assert parse_track({}) is None


def test_fill_missing_metadata():
    from hyprspot.webapi import fill_track
    bare = Track("spotify:track:x", "", "")
    full = Track("spotify:track:x", "Nome", "Artista", "Álbum", 1000, "http://img")
    assert fill_track(bare, {"spotify:track:x": full}) == full
    named = Track("spotify:track:x", "Já tem", "A")
    assert fill_track(named, {"spotify:track:x": full}) == named
    assert fill_track(bare, {}) == bare
    assert fill_track(None, {}) is None


def test_parse_recent_playlists_keeps_order_and_only_playlists():
    from hyprspot.webapi import parse_recent_playlists
    body = {"items": [
        {"context": {"type": "playlist", "uri": "spotify:playlist:a"}},
        {"context": {"type": "album", "uri": "spotify:album:x"}},
        {"context": None},
        {"context": {"type": "playlist", "uri": "spotify:playlist:b"}},
        {"context": {"type": "playlist", "uri": "spotify:playlist:a"}},
        {"context": {"type": "collection", "uri": "spotify:user:u:collection"}},
    ]}
    assert parse_recent_playlists(body) == ["spotify:playlist:a", "spotify:playlist:b"]
    assert parse_recent_playlists({}) == []


def test_parse_playlist():
    from hyprspot.webapi import parse_playlist
    p = parse_playlist({"uri": "spotify:playlist:a", "name": "A", "owner": {"display_name": "x"},
                        "images": [{"url": "http://i", "width": 300}]})
    assert (p.uri, p.name, p.owner, p.image_url) == ("spotify:playlist:a", "A", "x", "http://i")
    assert parse_playlist({}) is None
