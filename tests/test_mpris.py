from hyprspot import mpris
from hyprspot.model import Track
from hyprspot.state import Playback

TRACK = Track(uri="spotify:track:abc123", title="Californication", artist="Red Hot Chili Peppers",
              album="Californication", duration_ms=329000, art_url="https://i.scdn.co/image/x")


def _pb(**kw):
    base = dict(connected=True, logged_in=True, is_active=True, status="playing", track=TRACK)
    base.update(kw)
    return Playback(**base)


class Fake:
    def __init__(self, pb):
        self.pb = pb
        self.calls = []

    def playback(self):
        return self.pb

    def __getattr__(self, name):
        return lambda *a: self.calls.append((name, *a))


def test_status_follows_soloist_and_stays_behind_other_players_when_not_active():
    assert mpris.playback_status(_pb()) == "Playing"
    assert mpris.playback_status(_pb(status="buffering")) == "Playing"
    assert mpris.playback_status(_pb(status="paused")) == "Paused"
    # outro aparelho tocando, Soloist desconectado ou sem faixa: o playerctl prefere outros players
    assert mpris.playback_status(_pb(is_active=False)) == "Stopped"
    assert mpris.playback_status(_pb(connected=False)) == "Stopped"
    assert mpris.playback_status(_pb(track=None)) == "Stopped"


def test_metadata_has_title_artist_length_art_and_link():
    md = {k: v.unpack() for k, v in mpris.metadata(_pb()).items()}
    assert md["mpris:trackid"] == "/dev/hyprspot/track/spotify_track_abc123"
    assert md["xesam:title"] == "Californication"
    assert md["xesam:artist"] == ["Red Hot Chili Peppers"]
    assert md["mpris:length"] == 329_000_000
    assert md["mpris:artUrl"] == "https://i.scdn.co/image/x"
    assert md["xesam:url"] == "https://open.spotify.com/track/abc123"
    assert mpris.metadata(_pb(track=None))["mpris:trackid"].unpack() == mpris.NO_TRACK


def test_player_props_volume_loop_and_capabilities():
    p = {k: v.unpack() for k, v in mpris.player_props(_pb(volume=60, repeat="context", shuffle=True)).items()}
    assert p["Volume"] == 0.6 and p["LoopStatus"] == "Playlist" and p["Shuffle"] is True
    assert p["CanGoNext"] and p["CanSeek"] and p["CanControl"]
    off = {k: v.unpack() for k, v in mpris.player_props(Playback()).items()}
    assert not off["CanPlay"] and not off["CanGoNext"] and off["PlaybackStatus"] == "Stopped"


def test_media_keys_map_to_app_commands():
    f = Fake(_pb())
    for m in ("PlayPause", "Next", "Previous", "Pause", "Stop", "Play"):
        mpris.call(f, m, ())
    # tocando: Play não faz nada; Pause e Stop pausam
    assert [c[0] for c in f.calls] == ["play_pause", "next", "prev", "pause", "pause"]
    f = Fake(_pb(status="paused"))
    for m in ("Play", "Pause", "Stop"):
        mpris.call(f, m, ())
    assert [c[0] for c in f.calls] == ["play"]


def test_seek_and_set_position():
    f = Fake(_pb(status="paused", position_ms=10_000, speed=0.0))
    mpris.call(f, "Seek", (5_000_000,))
    mpris.call(f, "Seek", (-60_000_000,))
    mpris.call(f, "SetPosition", ("/dev/hyprspot/track/spotify_track_abc123", 42_000_000))
    mpris.call(f, "SetPosition", ("/dev/hyprspot/track/outra", 42_000_000))  # faixa já mudou: ignora
    mpris.call(f, "Seek", (400_000_000,))  # passou do fim: próxima
    assert f.calls == [("seek", 15_000), ("seek", 0), ("seek", 42_000), ("next",)]


def test_raise_and_quit():
    f = Fake(_pb())
    mpris.call(f, "Raise", ())
    mpris.call(f, "Quit", ())
    assert [c[0] for c in f.calls] == ["raise_", "quit"]
