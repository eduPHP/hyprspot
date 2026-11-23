import os

from hyprspot.cache import ArtCache, load_playlists, save_playlists
from hyprspot.model import Playlist


def test_playlists_roundtrip(tmp_path):
    items = [Playlist("spotify:playlist:a", "Ação", "eu", None), Playlist("spotify:playlist:b", "B", "x", "http://i")]
    save_playlists(tmp_path / "p.json", items)
    assert load_playlists(tmp_path / "p.json") == items


def test_corrupt_playlists_returns_empty(tmp_path):
    (tmp_path / "p.json").write_text("{nope")
    assert load_playlists(tmp_path / "p.json") == []
    assert load_playlists(tmp_path / "missing.json") == []


def test_art_evicts_oldest(tmp_path):
    c = ArtCache(tmp_path, max_bytes=10)
    paths = [c.put(f"http://img/{i}", b"abcd") for i in range(3)]
    for i, p in enumerate(paths):
        os.utime(p, (1000 + i, 1000 + i))
    freed = c.evict()
    assert freed == 4
    assert not paths[0].exists() and paths[1].exists() and paths[2].exists()


def test_get_touches_mtime(tmp_path):
    c = ArtCache(tmp_path)
    p = c.put("http://img/x", b"data")
    os.utime(p, (1000, 1000))
    assert c.get("http://img/x") == p
    assert p.stat().st_mtime > 1000
    assert c.get("http://img/missing") is None


def test_path_for_is_stable(tmp_path):
    c = ArtCache(tmp_path)
    assert c.path_for("u") == c.path_for("u") != c.path_for("v")


def test_played_playlists_roundtrip(tmp_path):
    from hyprspot.model import Playlist
    p = tmp_path / "state" / "played.json"
    items = [Playlist("spotify:playlist:a", "A", "x", None)]
    save_playlists(p, items)
    assert load_playlists(p) == items
