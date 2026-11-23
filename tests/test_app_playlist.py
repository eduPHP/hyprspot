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


class _FakeApp:
    """Só o que HyprspotApp._open_playlist usa."""

    def __init__(self, context_uri=None):
        from types import SimpleNamespace
        self.state = SimpleNamespace(playback=Playback(context_uri=context_uri))
        self.sent, self.opened, self.synced = [], [], 0
        self.expanded = SimpleNamespace(open_playlist=lambda p, tab, **kw: self.opened.append((p.uri, tab)))
        self.queue_sync = SimpleNamespace(request=self._sync)

    def _sync(self):
        self.synced += 1

    def on_command(self, name, fields):
        self.sent.append((name, fields))


def _open(app, tab="playlists"):
    from hyprspot.app import HyprspotApp
    from hyprspot.model import Playlist
    HyprspotApp._open_playlist(app, Playlist(P, "P", "eu", None), tab)


def test_clicking_another_playlist_plays_and_stays_on_the_list():
    for tab in ("playlists", "search"):
        app = _FakeApp(context_uri="spotify:playlist:x")
        _open(app, tab)
        assert app.sent == [("play", {"uri": P})]
        assert app.opened == []          # continua na lista
        assert app.synced == 1


def test_clicking_the_playing_playlist_opens_its_page():
    app = _FakeApp(context_uri=P)
    _open(app)
    assert app.sent == []
    assert app.opened == [(P, "playlists")]
