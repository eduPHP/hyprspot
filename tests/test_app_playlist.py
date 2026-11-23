"""Decisões da tela da playlist (funções puras usadas pelo app)."""
from hyprspot.model import Track
from hyprspot.state import Playback, play_button_commands, playlist_click_commands, skip_count

P = "spotify:playlist:p"


def test_click_on_current_playlist_only_opens():
    assert playlist_click_commands(Playback(context_uri=P), P) == []


def test_click_on_other_playlist_plays():
    assert playlist_click_commands(Playback(context_uri="spotify:playlist:x"), P) == [("play", {"uri": P})]
    assert playlist_click_commands(Playback(), P) == [("play", {"uri": P})]


def test_play_buttons_set_shuffle_first():
    assert play_button_commands(P, True) == [("set_shuffle", {"enabled": True}), ("play", {"uri": P})]
    assert play_button_commands(P, False) == [("set_shuffle", {"enabled": False}), ("play", {"uri": P})]


def test_skip_count():
    q = [Track(f"spotify:track:{i}", str(i), "") for i in range(15)]
    assert skip_count(q, "spotify:track:2") == 3       # 3 "próxima" chegam à 3ª da fila
    assert skip_count(q, "spotify:track:nada") is None
    assert skip_count(q, "spotify:track:11") is None   # além das 10 próximas


# --- playlists tocadas pela busca entram na aba Playlists ------------------------------------
from hyprspot.model import Playlist  # noqa: E402
from hyprspot.state import PLAYED_MAX, merge_playlists, remember_played  # noqa: E402

LIKED = Playlist("spotify:user:u:collection", "Músicas Curtidas", "Sua biblioteca", None)
LIB = [LIKED] + [Playlist(f"spotify:playlist:l{i}", f"L{i}", "eu", None) for i in range(4)]
NEW = Playlist("spotify:playlist:novo", "Nova", "alguém", None)


def test_played_playlist_shows_after_liked_songs():
    assert merge_playlists(LIB, [NEW]) == [LIKED, NEW] + LIB[1:]


def test_played_playlist_already_in_library_is_not_repeated():
    assert merge_playlists(LIB, [LIB[2], NEW]) == [LIKED, NEW] + LIB[1:]


def test_merge_without_liked_songs():
    assert merge_playlists(LIB[1:], [NEW]) == [NEW] + LIB[1:]


def test_remember_played_puts_latest_first_and_caps():
    played = remember_played([], NEW)
    other = Playlist("spotify:playlist:outra", "Outra", "x", None)
    assert remember_played(played, other) == [other, NEW]
    assert remember_played([other, NEW], NEW) == [NEW, other]
    many = [Playlist(f"spotify:playlist:m{i}", "", "", None) for i in range(PLAYED_MAX)]
    assert len(remember_played(many, NEW)) == PLAYED_MAX
    assert remember_played(many, NEW)[0] == NEW


def test_liked_songs_is_never_remembered():
    assert remember_played([], LIKED) == []


def test_playing_from_search_adds_it_to_the_playlists_tab(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import hyprspot.app as appmod
    from hyprspot.app import HyprspotApp
    from hyprspot.cache import load_playlists
    from hyprspot.state import AppState
    monkeypatch.setattr(appmod, "PLAYED_PATH", tmp_path / "played.json")
    state = AppState()
    app = SimpleNamespace(state=state, _library=[], _played=[], on_command=lambda *a: None,
                          expanded=SimpleNamespace(open_playlist=lambda *a, **k: None),
                          queue_sync=SimpleNamespace(request=lambda: None))
    for name in ("_set_library", "_remember_played", "_save_played"):
        setattr(app, name, getattr(HyprspotApp, name).__get__(app))
    app._set_library(LIB)
    HyprspotApp._open_playlist(app, LIB[3], "playlists")   # da própria lista: nada muda
    assert state.playlists == LIB
    HyprspotApp._open_playlist(app, NEW, "search")
    assert state.playlists == [LIKED, NEW] + LIB[1:]
    assert load_playlists(tmp_path / "played.json") == [NEW]
    app._set_library(LIB)                                    # a Web API recarrega: continua lá
    assert NEW in state.playlists


def test_sync_played_puts_spotify_recents_first_and_keeps_local_ones():
    from hyprspot.state import sync_played
    a, b, c = (Playlist(f"spotify:playlist:{x}", x, "", None) for x in "abc")
    assert sync_played([c, a], [a, b]) == [a, b, c]
    many = [Playlist(f"spotify:playlist:m{i}", "", "", None) for i in range(PLAYED_MAX)]
    assert len(sync_played(many, [a])) == PLAYED_MAX


def test_sync_recent_brings_playlists_played_elsewhere(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import hyprspot.app as appmod
    from hyprspot.app import HyprspotApp
    from hyprspot.state import AppState
    monkeypatch.setattr(appmod, "PLAYED_PATH", tmp_path / "played.json")
    phone = Playlist("spotify:playlist:celular", "Do celular", "x", None)
    asked = []
    api = SimpleNamespace(
        store=SimpleNamespace(has_scope=lambda s: True),
        recent_playlists=lambda cb: cb([phone.uri, LIB[2].uri, NEW.uri]),
        playlist=lambda uri, cb: (asked.append(uri), cb(phone if uri == phone.uri else None)))
    state = AppState()
    app = SimpleNamespace(state=state, api=api, _library=[], _played=[NEW])
    for name in ("_set_library", "_save_played", "_sync_recent"):
        setattr(app, name, getattr(HyprspotApp, name).__get__(app))
    app._set_library(LIB)
    app._sync_recent()
    assert asked == [phone.uri]                 # da biblioteca e já conhecidas: sem chamada
    assert state.playlists == [LIKED, phone, NEW] + LIB[1:]


def test_sync_recent_skips_without_permission():
    from types import SimpleNamespace
    from hyprspot.app import HyprspotApp
    called = []
    app = SimpleNamespace(api=SimpleNamespace(store=SimpleNamespace(has_scope=lambda s: False),
                                              recent_playlists=lambda cb: called.append(1)))
    HyprspotApp._sync_recent(app)
    assert called == []
