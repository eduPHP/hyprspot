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
