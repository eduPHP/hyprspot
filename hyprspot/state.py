"""Estado da aplicação: reducer puro dos eventos do Soloist, otimismo e throttle."""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Callable

import gi

gi.require_version("GObject", "2.0")
from gi.repository import GObject  # noqa: E402

from hyprspot.model import Playlist, Track  # noqa: E402
from hyprspot.soloist import entity_to_track, parse_queue  # noqa: E402


@dataclass(frozen=True)
class Playback:
    connected: bool = False
    logged_in: bool = False
    is_active: bool = False
    status: str = "idle"
    track: Track | None = None
    context_uri: str | None = None
    context_name: str | None = None
    position_ms: int = 0
    position_ts_ms: int = 0
    speed: float = 0.0
    volume: int = 50
    shuffle: bool = False
    repeat: str = "off"
    device_name: str = "hyprspot"


def clock_ms() -> int:
    return int(time.time() * 1000)


def _position(pb: Playback, pos: dict | None) -> Playback:
    if not pos:
        return pb
    return replace(pb, position_ms=int(pos.get("position_ms", 0)),
                   position_ts_ms=int(pos.get("timestamp_ms", 0)), speed=float(pos.get("speed", 0) or 0))


def _options(pb: Playback, opts: dict | None) -> Playback:
    if not opts:
        return pb
    return replace(pb, shuffle=bool(opts.get("shuffle", pb.shuffle)), repeat=opts.get("repeat", pb.repeat))


def apply_event(pb: Playback, ev: dict) -> Playback:
    t = ev.get("type")
    if t == "_connected":
        return replace(pb, connected=True)
    if t == "_disconnected":
        return replace(pb, connected=False, logged_in=False, status="idle", speed=0.0, track=None)
    if t == "auth_state":
        return replace(pb, logged_in=bool(ev.get("logged_in")), is_active=bool(ev.get("is_active", pb.is_active)),
                       device_name=ev.get("device_name") or pb.device_name)
    if t == "playback_state":
        pb = replace(pb, status=ev.get("status", pb.status),
                     track=entity_to_track(ev.get("item")),
                     context_uri=(ev.get("context") or {}).get("uri") or None,
                     volume=int(ev.get("volume", pb.volume)), is_active=bool(ev.get("is_active", pb.is_active)))
        return _options(_position(pb, ev.get("position")), ev.get("options"))
    if t == "playback_changed":
        return replace(pb, status=ev.get("status", pb.status))
    if t == "track_changed":
        return replace(pb, track=entity_to_track(ev.get("item")))
    if t == "context_changed":
        return replace(pb, context_uri=(ev.get("context") or {}).get("uri") or None)
    if t == "volume_changed":
        return replace(pb, volume=int(ev.get("volume", pb.volume)))
    if t == "device_changed":
        return replace(pb, is_active=bool(ev.get("is_active")), device_name=ev.get("device_name") or pb.device_name)
    if t == "options_changed":
        return _options(pb, ev.get("options"))
    if t == "position_sync":
        return _position(pb, ev.get("position"))
    return pb


def position_now(pb: Playback, at_ms: int) -> int:
    pos = pb.position_ms + (at_ms - pb.position_ts_ms) * pb.speed if pb.speed else pb.position_ms
    hi = pb.track.duration_ms if pb.track and pb.track.duration_ms else None
    pos = max(0, int(pos))
    return min(pos, hi) if hi is not None else pos


def optimistic(pb: Playback, command: str, now_ms: int | None = None, **fields) -> Playback:
    if command == "play":
        return replace(pb, status="playing")
    if command == "pause":
        return replace(pb, status="paused")
    if command in ("skip_next", "skip_prev"):
        return replace(pb, status="buffering")
    if command == "set_volume":
        return replace(pb, volume=int(fields["volume"]))
    if command == "set_shuffle":
        return replace(pb, shuffle=bool(fields["enabled"]))
    if command == "seek":
        at = clock_ms() if now_ms is None else now_ms
        return replace(pb, position_ms=int(fields["position_ms"]), position_ts_ms=at)
    return pb


SKIP_WINDOW_MS = 1200  # faixas com erro somem em 0,5–0,9 s; as que tocam passam bem disso


class SkipDetector:
    """Reconhece faixas que o Soloist pulou sozinho (ex.: licença negada, erro 8001).

    O Soloist não avisa o erro pelo WebSocket; o sinal é a faixa ser trocada logo depois de
    aparecer sem que o usuário tenha pedido nada desde que ela apareceu.
    """

    def __init__(self, window_ms: int = SKIP_WINDOW_MS):
        self.window_ms = window_ms
        self._track: Track | None = None
        self._since = 0
        self._user_at: int | None = None

    def user_command(self, now_ms: int) -> None:
        self._user_at = now_ms

    def track_changed(self, track: Track | None, now_ms: int) -> Track | None:
        """Registra a faixa atual; devolve a anterior se ela foi pulada pelo Soloist."""
        if track is None or (self._track is not None and track.uri == self._track.uri):
            return None
        prev, since = self._track, self._since
        self._track, self._since = track, now_ms
        if prev is None or now_ms - since >= self.window_ms:
            return None
        if self._user_at is not None and self._user_at >= since:
            return None
        return prev


def skip_message(titles: list[str]) -> str:
    if len(titles) > 1:
        return f"{len(titles)} faixas indisponíveis, puladas"
    return f"“{titles[0]}” indisponível, pulada" if titles and titles[0] else "Faixa indisponível, pulada"


class QueueSync:
    """Mantém a fila inteira do Soloist sem cair em laço.

    O `queue_changed` espontâneo traz no máximo 10 entradas; a fila inteira vem pedindo
    `get_queue` (limit 0), cuja resposta também chega como `queue_changed`.
    """

    def __init__(self, send: Callable[[str, dict], bool]):
        self._send = send
        self._pending = False

    def reset(self) -> None:
        """Esquece o pedido pendente (desconexão ou erro: a resposta não vai chegar)."""
        self._pending = False

    def request(self) -> None:
        if not self._pending:
            self._pending = bool(self._send("get_queue", {"limit": 0}))

    def on_queue_event(self, ev: dict) -> list[Track] | None:
        if self._pending:
            self._pending = False
            return parse_queue(ev)
        self.request()
        return None


def playlist_click_commands(pb: Playback, uri: str) -> list[tuple[str, dict]]:
    """Clicar numa playlist toca; se ela já é o que está tocando, só abre a tela."""
    return [] if pb.context_uri == uri else [("play", {"uri": uri})]


def play_button_commands(uri: str, shuffle: bool) -> list[tuple[str, dict]]:
    return [("set_shuffle", {"enabled": shuffle}), ("play", {"uri": uri})]


SKIP_FALLBACK_MAX = 10
SPONTANEOUS_QUEUE_MAX = 10  # o queue_changed espontâneo do Soloist traz no máximo 10


def queue_is_truncated(old: list[Track], new: list[Track]) -> bool:
    """`new` é só o recorte de 10 do evento espontâneo de uma fila maior que já temos."""
    n = len(new)
    if not new or n > SPONTANEOUS_QUEUE_MAX or len(old) <= n:
        return False
    return new == old[:n] or new == old[1:1 + n]  # a faixa pode ter avançado uma


def skip_count(queue: list[Track], uri: str) -> int | None:
    """Quantos 'próxima' até a música (alternativa quando a Web API não consegue tocá-la direto)."""
    for i, t in enumerate(queue[:SKIP_FALLBACK_MAX]):
        if t.uri == uri:
            return i + 1
    return None


PLAYLISTS_MAX_AGE_S = 600


def should_refresh_playlists(last_ok: float | None, now: float, has_items: bool) -> bool:
    """Recarregar quando nunca deu certo, quando a lista está vazia ou quando passou de 10 min."""
    return last_ok is None or not has_items or now - last_ok > PLAYLISTS_MAX_AGE_S


class Throttle:
    """Deixa passar no máximo um valor por intervalo; flush entrega o último pendente."""

    def __init__(self, interval_ms: int = 100):
        self.interval_ms = interval_ms
        self._last_sent_ms: int | None = None
        self._pending = None
        self._has_pending = False

    def push(self, value, at_ms: int):
        if self._last_sent_ms is None or at_ms - self._last_sent_ms >= self.interval_ms:
            self._last_sent_ms = at_ms
            self._has_pending = False
            return value
        self._pending, self._has_pending = value, True
        return None

    def flush(self, at_ms: int):
        if not self._has_pending:
            return None
        self._has_pending = False
        self._last_sent_ms = at_ms
        return self._pending


class AppState(GObject.Object):
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self):
        super().__init__()
        self.playback = Playback()
        self.playlists: list[Playlist] = []
        self.search_query = ""
        self.search_results: list[Track] = []
        self.search_playlists: list[Playlist] = []
        self.ui_mode = "compact"
        self.tab = "playlists"
        self.error: str | None = None
        self.playlists_error: str | None = None
        self.liked: bool | None = None
        self.soloist_days_left: int | None = None
        self.queue: list[Track] = []
        self.queue_context: str | None = None

    def set_playback(self, pb: Playback) -> None:
        if pb != self.playback:
            self.playback = pb
            self.emit("changed", "playback")

    def set_playlists(self, items: list[Playlist]) -> None:
        if items != self.playlists:
            self.playlists = list(items)
            self.emit("changed", "playlists")

    def set_playlists_error(self, msg: str | None) -> None:
        if msg != self.playlists_error:
            self.playlists_error = msg
            self.emit("changed", "playlists")

    def set_queue(self, context_uri: str | None, tracks: list[Track]) -> None:
        if (context_uri, tracks) != (self.queue_context, self.queue):
            self.queue_context, self.queue = context_uri, list(tracks)
            self.emit("changed", "queue")

    def set_search(self, query: str, results: list[Track], playlists: list[Playlist] | None = None) -> None:
        """`playlists=None` mantém as atuais (páginas seguintes da busca só trazem músicas)."""
        pls = self.search_playlists if playlists is None else list(playlists)
        if (query, results, pls) != (self.search_query, self.search_results, self.search_playlists):
            self.search_query, self.search_results, self.search_playlists = query, list(results), pls
            self.emit("changed", "search")

    def set_ui(self, mode: str | None = None, tab: str | None = None) -> None:
        new = (mode or self.ui_mode, tab or self.tab)
        if new != (self.ui_mode, self.tab):
            self.ui_mode, self.tab = new
            self.emit("changed", "ui")

    def set_error(self, msg: str | None) -> None:
        if msg != self.error:
            self.error = msg
            self.emit("changed", "error")

    def set_soloist_days(self, days: int | None) -> None:
        if days != self.soloist_days_left:
            self.soloist_days_left = days
            self.emit("changed", "soloist")

    def set_liked(self, liked: bool | None) -> None:
        if liked != self.liked:
            self.liked = liked
            self.emit("changed", "liked")
