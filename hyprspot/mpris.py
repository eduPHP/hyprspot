"""Player MPRIS: o hyprspot aparece para o playerctl, o swayosd e painéis.

As teclas de mídia do Omarchy chamam `swayosd-client --playerctl ...`, que manda o comando
para o player MPRIS ativo. Com o hyprspot registrado como `org.mpris.MediaPlayer2.hyprspot`,
as mesmas teclas controlam o Soloist quando ele está tocando, sem desfazer os atalhos do
sistema: o navegador e outros players continuam recebendo as teclas quando são eles que tocam.

Quando o Soloist não é o aparelho ativo do Spotify, o hyprspot diz "Stopped" e assim fica atrás
de qualquer player tocando ou pausado na escolha do playerctl.
"""
from __future__ import annotations

import re
from typing import Callable, Protocol

from gi.repository import Gio, GLib

from hyprspot.state import Playback, clock_ms, position_now

BUS_NAME = "org.mpris.MediaPlayer2.hyprspot"
OBJECT_PATH = "/org/mpris/MediaPlayer2"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
SEEKED_JUMP_US = 1_500_000  # mais que isso de diferença do previsto = alguém mudou a posição

XML = f"""
<node>
  <interface name="{ROOT_IFACE}">
    <method name="Raise"/>
    <method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="{PLAYER_IFACE}">
    <method name="Next"/>
    <method name="Previous"/>
    <method name="Pause"/>
    <method name="PlayPause"/>
    <method name="Stop"/>
    <method name="Play"/>
    <method name="Seek"><arg direction="in" type="x" name="Offset"/></method>
    <method name="SetPosition">
      <arg direction="in" type="o" name="TrackId"/>
      <arg direction="in" type="x" name="Position"/>
    </method>
    <method name="OpenUri"><arg direction="in" type="s" name="Uri"/></method>
    <signal name="Seeked"><arg type="x" name="Position"/></signal>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="LoopStatus" type="s" access="read"/>
    <property name="Rate" type="d" access="read"/>
    <property name="Shuffle" type="b" access="read"/>
    <property name="Metadata" type="a{{sv}}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>
"""


class Controls(Protocol):
    """O que o MPRIS pede ao app (HyprspotApp implementa via MprisBridge)."""

    def playback(self) -> Playback: ...
    def play_pause(self) -> None: ...
    def play(self) -> None: ...
    def pause(self) -> None: ...
    def next(self) -> None: ...
    def prev(self) -> None: ...
    def seek(self, position_ms: int) -> None: ...
    def set_volume(self, volume: int) -> None: ...
    def raise_(self) -> None: ...
    def quit(self) -> None: ...


# ---------------------------------------------------------------------- puro (testável)
def playback_status(pb: Playback) -> str:
    if not (pb.connected and pb.logged_in and pb.is_active) or pb.track is None:
        return "Stopped"
    return "Playing" if pb.status in ("playing", "buffering") else "Paused"


def track_id(uri: str | None) -> str:
    if not uri:
        return NO_TRACK
    return "/dev/hyprspot/track/" + (re.sub(r"[^A-Za-z0-9_]", "_", uri) or "_")


def loop_status(repeat: str) -> str:
    return {"track": "Track", "context": "Playlist"}.get(repeat, "None")


def metadata(pb: Playback) -> dict[str, GLib.Variant]:
    t = pb.track
    if t is None:
        return {"mpris:trackid": GLib.Variant("o", NO_TRACK)}
    md = {"mpris:trackid": GLib.Variant("o", track_id(t.uri)),
          "xesam:title": GLib.Variant("s", t.title or "")}
    if t.artist:
        md["xesam:artist"] = GLib.Variant("as", [t.artist])
    if t.album:
        md["xesam:album"] = GLib.Variant("s", t.album)
    if t.duration_ms:
        md["mpris:length"] = GLib.Variant("x", t.duration_ms * 1000)
    if t.art_url:
        md["mpris:artUrl"] = GLib.Variant("s", t.art_url)
    parts = t.uri.split(":")
    if len(parts) == 3 and parts[0] == "spotify":
        md["xesam:url"] = GLib.Variant("s", f"https://open.spotify.com/{parts[1]}/{parts[2]}")
    return md


def player_props(pb: Playback) -> dict[str, GLib.Variant]:
    """Propriedades do Player que mudam com o estado (Position fica de fora, como manda a spec)."""
    ok = pb.connected and pb.logged_in
    has = ok and pb.track is not None
    return {
        "PlaybackStatus": GLib.Variant("s", playback_status(pb)),
        "LoopStatus": GLib.Variant("s", loop_status(pb.repeat)),
        "Shuffle": GLib.Variant("b", pb.shuffle),
        "Metadata": GLib.Variant("a{sv}", metadata(pb)),
        "Volume": GLib.Variant("d", max(0, min(100, pb.volume)) / 100),
        "CanGoNext": GLib.Variant("b", has),
        "CanGoPrevious": GLib.Variant("b", has),
        "CanPlay": GLib.Variant("b", ok),
        "CanPause": GLib.Variant("b", ok),
        "CanSeek": GLib.Variant("b", has and bool(pb.track.duration_ms)),
        "CanControl": GLib.Variant("b", True),
    }


STATIC = {
    ROOT_IFACE: {
        "CanQuit": GLib.Variant("b", True),
        "CanRaise": GLib.Variant("b", True),
        "HasTrackList": GLib.Variant("b", False),
        "Identity": GLib.Variant("s", "hyprspot"),
        "SupportedUriSchemes": GLib.Variant("as", []),
        "SupportedMimeTypes": GLib.Variant("as", []),
    },
    PLAYER_IFACE: {
        "Rate": GLib.Variant("d", 1.0),
        "MinimumRate": GLib.Variant("d", 1.0),
        "MaximumRate": GLib.Variant("d", 1.0),
    },
}


def position_us(pb: Playback, now_ms: int) -> int:
    return position_now(pb, now_ms) * 1000 if pb.track else 0


def call(ctl: Controls, method: str, args: tuple) -> None:
    """Executa um método do MPRIS no app."""
    pb = ctl.playback()
    if method == "PlayPause":
        ctl.play_pause()
    elif method == "Play":
        if playback_status(pb) != "Playing":
            ctl.play()
    elif method in ("Pause", "Stop"):  # o Spotify não tem "parar": pausar é o mais próximo
        if playback_status(pb) == "Playing":
            ctl.pause()
    elif method == "Next":
        ctl.next()
    elif method == "Previous":
        ctl.prev()
    elif method == "Seek" and pb.track:
        target = position_now(pb, clock_ms()) + args[0] // 1000
        if pb.track.duration_ms and target >= pb.track.duration_ms:
            ctl.next()
        else:
            ctl.seek(max(0, target))
    elif method == "SetPosition" and pb.track:
        tid, pos = args
        if tid == track_id(pb.track.uri) and 0 <= pos // 1000 <= (pb.track.duration_ms or pos // 1000):
            ctl.seek(pos // 1000)
    elif method == "Raise":
        ctl.raise_()
    elif method == "Quit":
        ctl.quit()


# ---------------------------------------------------------------------- D-Bus
class MprisServer:
    """Registra o hyprspot no barramento da sessão e avisa as mudanças."""

    def __init__(self, ctl: Controls, now_ms: Callable[[], int] = clock_ms):
        self.ctl = ctl
        self.now_ms = now_ms
        self._conn: Gio.DBusConnection | None = None
        self._reg: list[int] = []
        self._own_id = 0
        self._last: dict[str, GLib.Variant] = {}
        self._last_pos: tuple[str | None, int, int] | None = None  # (faixa, posição µs, quando ms)

    def start(self, conn: Gio.DBusConnection | None = None) -> bool:
        conn = conn or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        if conn is None:
            return False
        self._conn = conn
        info = Gio.DBusNodeInfo.new_for_xml(XML)
        # register_object com funções Python está obsoleto no PyGObject 3.52+
        register = getattr(conn, "register_object_with_closures2", None) or conn.register_object
        for iface in info.interfaces:
            self._reg.append(register(OBJECT_PATH, iface, self._on_call, self._on_get, self._on_set))
        self._own_id = Gio.bus_own_name_on_connection(conn, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, None)
        self._last = player_props(self.ctl.playback())
        self._remember_position()
        return True

    def stop(self) -> None:
        if self._own_id:
            Gio.bus_unown_name(self._own_id)
            self._own_id = 0
        if self._conn:
            for r in self._reg:
                self._conn.unregister_object(r)
        self._reg = []
        self._conn = None

    # ------------------------------------------------------------------ do app para o barramento
    def changed(self) -> None:
        """Chamado a cada mudança de playback: manda PropertiesChanged só do que mudou."""
        if self._conn is None:
            return
        props = player_props(self.ctl.playback())
        diff = {k: v for k, v in props.items() if k not in self._last or not v.equal(self._last[k])}
        self._last = props
        if diff:
            self._conn.emit_signal(None, OBJECT_PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                   GLib.Variant("(sa{sv}as)", (PLAYER_IFACE, diff, [])))
        self._check_seeked()

    def _remember_position(self) -> None:
        pb = self.ctl.playback()
        now = self.now_ms()
        self._last_pos = (pb.track.uri if pb.track else None, position_us(pb, now), now)

    def _check_seeked(self) -> None:
        """Seeked: a posição pulou (barra arrastada, comando de outro aparelho), não só andou."""
        pb = self.ctl.playback()
        now = self.now_ms()
        prev = self._last_pos
        pos = position_us(pb, now)
        uri = pb.track.uri if pb.track else None
        if prev and prev[0] == uri and uri is not None:
            playing = playback_status(pb) == "Playing"
            expected = prev[1] + ((now - prev[2]) * 1000 if playing else 0)
            if abs(pos - expected) > SEEKED_JUMP_US:
                self._conn.emit_signal(None, OBJECT_PATH, PLAYER_IFACE, "Seeked", GLib.Variant("(x)", (pos,)))
        self._last_pos = (uri, pos, now)

    # ------------------------------------------------------------------ do barramento para o app
    def _on_call(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        try:
            if method != "OpenUri":
                call(self.ctl, method, params.unpack())
            invocation.return_value(None)
        except Exception as e:  # noqa: BLE001 — um erro aqui não pode derrubar o app
            invocation.return_dbus_error("org.mpris.MediaPlayer2.hyprspot.Error", str(e))

    def _on_get(self, _conn, _sender, _path, iface, prop):
        if prop == "Position":
            return GLib.Variant("x", position_us(self.ctl.playback(), self.now_ms()))
        if prop in STATIC.get(iface, {}):
            return STATIC[iface][prop]
        return player_props(self.ctl.playback()).get(prop)

    def _on_set(self, _conn, _sender, _path, _iface, prop, value) -> bool:
        if prop == "Volume":
            self.ctl.set_volume(round(max(0.0, min(1.0, value.unpack())) * 100))
            return True
        return False
