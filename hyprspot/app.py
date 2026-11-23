"""Aplicação: instância única, ações, ligação Soloist ↔ estado ↔ UI ↔ Web API."""
from __future__ import annotations

import subprocess
import time
from datetime import date
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Soup", "3.0")
from gi.repository import Gio, GLib, Gtk, Soup  # noqa: E402

from hyprspot import __version__, update  # noqa: E402
from hyprspot.auth import TokenStore  # noqa: E402
from hyprspot.cache import ArtCache, load_playlists, save_playlists  # noqa: E402
from hyprspot.cli import ACTIONS, parse_args  # noqa: E402
from hyprspot import onboarding as ob  # noqa: E402
from hyprspot import waybar  # noqa: E402
from hyprspot.mpris import MprisServer  # noqa: E402
from hyprspot.config import (CACHE_DIR, CONFIG_PATH, DEFAULT_MARGIN, PLAYED_PATH, TOKEN_PATH, load_config,  # noqa: E402
                             save_margin, update_config)
from hyprspot.focus import FocusWatcher, hypr_cursor, hypr_outputs  # noqa: E402
from hyprspot.soloist import SoloistClient, repeat_commands  # noqa: E402
from hyprspot.soloistctl import days_left, installed_version  # noqa: E402
from hyprspot.state import (AppState, QueueSync, SkipDetector, Throttle, apply_event, clock_ms,  # noqa: E402
                            merge_playlists, optimistic, play_button_commands, playlist_click_commands,
                            queue_is_truncated, remember_played,
                            should_refresh_playlists, skip_count, skip_message)
from hyprspot.ui.common import load_css  # noqa: E402
from hyprspot.ui.compact import CompactView, compact_key_action, context_name  # noqa: E402
from hyprspot.ui.cover import ArtLoader  # noqa: E402
from hyprspot.ui.expanded import ExpandedView  # noqa: E402
from hyprspot.ui.settings import SettingsView  # noqa: E402
from hyprspot.ui import theme  # noqa: E402
from hyprspot.ui.window import HyprspotWindow  # noqa: E402
from dataclasses import replace  # noqa: E402

from hyprspot.webapi import WebApi, fill_track, liked_playlist, soup_transport  # noqa: E402

APP_ID = "dev.hyprspot.App"
ERROR_CLEAR_MS = 2500
SKIP_NOTICE_MS = 4000
VOLUME_FLUSH_MS = 110
SOLOIST_CHECK_S = 6 * 3600
ROOT = Path(__file__).resolve().parent.parent
BAR_REOPEN_GUARD_S = 0.4  # clicar no item com o player aberto: o "clicou fora" recolhe antes do clique chegar
VOLUME_STEP = 5


def install_waybar() -> None:
    """Itens e estilo na Waybar (idempotente; versões novas acrescentam o que faltar)."""
    done = waybar.install(bin_dir())
    if "config" in done:
        # a Waybar relê a config com SIGUSR2 (o estilo ela recarrega sozinha)
        subprocess.run(["pkill", "-SIGUSR2", "-x", "waybar"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def bin_dir() -> Path:
    """Onde a Waybar acha o hyprspot-ctl: o link do install.sh, ou o próprio repositório."""
    local = Path.home() / ".local" / "bin"
    return local if (local / "hyprspot-ctl").exists() else ROOT / "bin"


def opacity_css(opacity: float) -> str:
    """Fundo do widget com a transparência escolhida (style.css tem os valores padrão, 0,94)."""
    o = opacity
    well = min(1.0, o + 0.06)  # abas e campos: um pouco mais sólidos que o cartão
    return (f".hs-root {{ background-color: alpha(@hs_base, {o:.2f}); }}\n"
            f".hs-expanded {{ background-color: alpha(@hs_base, {min(1.0, o + 0.03):.2f}); }}\n"
            f".hs-footer {{ background-color: alpha(@hs_bg, {0.6 * o / 0.94:.2f}); }}\n"
            f".hs-tabs, searchentry.hs-search, entry.hs-search, .hs-key {{ background-color: alpha(@hs_bg, {well:.2f}); }}\n")


class SettingsBridge:
    """O que a aba Ajustes pode fazer no app (ver hyprspot.ui.settings.SettingsActions)."""

    def __init__(self, app: "HyprspotApp"):
        self.app = app

    def config(self):
        return load_config()

    def preview_opacity(self, value: float) -> None:
        self.app.config.opacity = value
        theme.refresh(self.app.opacity_provider)

    def set_opacity(self, value: float) -> None:
        self.preview_opacity(value)
        update_config(CONFIG_PATH, opacity=value)

    def set_blur(self, on: bool) -> None:
        self.app.config.blur = on
        update_config(CONFIG_PATH, blur=on)
        self.app.win.set_blur(on)
        if self.app.state.ui_mode == "expanded":
            # some e volta para trocar o namespace: perde o foco e recolheria; reabre na aba
            GLib.idle_add(lambda: (self.app.run_action("expand-settings"), False)[1])

    def set_anchor(self, anchor: str) -> None:
        self.app.win.set_anchor(anchor, DEFAULT_MARGIN)
        self.app.config.anchor = anchor
        update_config(CONFIG_PATH, anchor=anchor, margin=tuple(self.app.win.margin))

    def set_monitor(self, name: str) -> None:
        changes = {"monitor": name}
        if name and self.app.win.move_to_output(name):
            changes["margin"] = tuple(self.app.win.margin)
        update_config(CONFIG_PATH, **changes)

    def set_placement(self, placement: str) -> None:
        self.app.config.placement = placement
        update_config(CONFIG_PATH, placement=placement)
        if placement == "waybar":
            install_waybar()
        self.app.win.set_placement(placement)
        self.app.expanded.set_dropdown(placement == "waybar")
        self.app.update_bar()

    def monitors(self) -> list[str]:
        return [o.name for o in sorted(hypr_outputs(), key=lambda o: (o.x, o.y))]

    def key_hint(self) -> str | None:
        return ob.read_key()

    def save_key(self, text: str) -> str | None:
        key = ob.clean_key(text)
        if not key:
            return "Isso não parece uma chave do Soloist. Copie de novo no painel do Spotify."
        if key == ob.read_key():
            return "Essa já é a chave em uso."
        ob.write_soloist_env(key)
        subprocess.Popen(["sh", "-c", ob.RESTART_SOLOIST], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return None

    def open_key_page(self) -> None:
        Gio.AppInfo.launch_default_for_uri(ob.SOLOIST_KEY_URL, None)

    def open_setup(self) -> None:
        subprocess.Popen([str(ROOT / "bin" / "hyprspot"), "setup"], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class MprisBridge:
    """O que as teclas de mídia (via MPRIS) podem fazer no app (ver hyprspot.mpris.Controls)."""

    def __init__(self, app: "HyprspotApp"):
        self.app = app

    def playback(self):
        return self.app.state.playback

    def play_pause(self) -> None:
        self.app.run_action("play-pause")

    def play(self) -> None:
        self.app.on_command("play", {})

    def pause(self) -> None:
        self.app.on_command("pause", {})

    def next(self) -> None:
        self.app.run_action("next")

    def prev(self) -> None:
        self.app.run_action("prev")

    def seek(self, position_ms: int) -> None:
        self.app.on_command("seek", {"position_ms": position_ms})

    def set_volume(self, volume: int) -> None:
        self.app.on_command("set_volume", {"volume": volume})

    def raise_(self) -> None:
        self.app.run_action("expand")

    def quit(self) -> None:
        self.app.run_action("quit")


class HyprspotApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.win: HyprspotWindow | None = None
        self._error_id = 0
        self._collapse_id = 0
        self._vol_id = 0
        self._last_track_uri: str | None = None
        self._search_seq = 0
        self._playlists_ok: float | None = None
        self._playlists_loading = False
        self._library: list = []   # Curtidas + playlists da conta (Web API)
        self._played: list = []    # tocadas pela busca (PLAYED_PATH)
        self._meta: dict = {}
        self._meta_pending: set[str] = set()
        self._hide_hint_shown = False
        self.skips = SkipDetector()
        self._skipped: list[str] = []
        for name in (*ACTIONS.values(), "expand", "expand-search", "expand-settings", "click-anywhere",
                     "hide-toggle"):
            if self.lookup_action(name) is not None:
                continue  # inclui "quit"
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda _a, _p, n=name: self.run_action(n))
            self.add_action(act)
        open_url = Gio.SimpleAction.new("open-url", GLib.VariantType.new("s"))
        open_url.connect("activate", lambda _a, url: Gio.AppInfo.launch_default_for_uri(url.get_string(), None))
        self.add_action(open_url)

    # ------------------------------------------------------------------ ciclo de vida
    def do_command_line(self, cmdline: Gio.ApplicationCommandLine) -> int:
        cmd, opts = parse_args(cmdline.get_arguments()[1:])
        if cmd == "quit" and self.win is None:
            return 0  # nada rodando: não abre só para fechar
        if self.win is None:
            self._build()
        if cmd == "expand":
            self.run_action("expand-search" if opts.get("search") else "expand")
        elif cmd == "settings":
            self.run_action("expand-settings")
        elif cmd in ACTIONS:
            self.run_action(ACTIONS[cmd])
        return 0

    def _build(self) -> None:
        self.hold()
        load_css()
        self.config = load_config()
        self.opacity_provider = theme.load_text(lambda: opacity_css(self.config.opacity),
                                                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
        self.state = AppState()
        self.client = SoloistClient(self.config.soloist_ws)
        self.session = Soup.Session()
        self.api = WebApi(TokenStore(TOKEN_PATH), self.session)
        self.art = ArtLoader(ArtCache(CACHE_DIR / "art"), self.session)
        self.focus = FocusWatcher()
        self.volume = Throttle(100)
        self.queue_sync = QueueSync(lambda name, fields: self.client.send(name, **fields))

        self.compact = CompactView(self.state, self.on_command, lambda: self.run_action("expand"), self.art)
        self.expanded = ExpandedView(self.state, self.on_command, self.on_search,
                                     lambda: self.run_action("collapse"), self.on_like, self.art,
                                     on_quit=lambda: self.run_action("quit"),
                                     on_open_playlist=self._open_playlist,
                                     on_play_playlist=self._play_playlist,
                                     on_pick_track=self._pick_track,
                                     settings=SettingsView(self.state, SettingsBridge(self)))
        self.win = HyprspotWindow(self, self.config, self.compact, self.expanded,
                                  lambda m: save_margin(CONFIG_PATH, m, self.win.output), self._on_compact_key,
                                  on_push_offscreen=self._hide_hint)
        self.win.connect("notify::is-active", self._on_active_changed)
        self.expanded.set_dropdown(self.config.placement == "waybar")
        self._collapsed_at = 0.0
        self.state.connect("changed", lambda _s, what: what in ("playback", "soloist", "ui", "liked") and self.update_bar())
        self.focus.connect("focus-lost", lambda *_: self._schedule_collapse())

        # teclas de mídia: o playerctl/swayosd do sistema acha o hyprspot como player MPRIS
        self.mpris = MprisServer(MprisBridge(self))
        self.mpris.start(self.get_dbus_connection())
        self.state.connect("changed", lambda _s, what: what in ("playback", "soloist") and self.mpris.changed())

        self.client.connect("event", self._on_event)
        self.client.connect("connection-changed", self._on_connection)
        GLib.timeout_add_seconds(1, self._tick_bar)
        if self.config.placement == "waybar":
            install_waybar()
            self.win.set_visible(False)  # só aparece quando clicam no item da barra
        else:
            self.win.present()
        self.update_bar()
        self.client.start()

        self._played = load_playlists(PLAYED_PATH)
        self._set_library(load_playlists(CACHE_DIR / "playlists.json"))
        self.refresh_playlists()
        self._check_soloist()
        GLib.timeout_add_seconds(SOLOIST_CHECK_S, lambda: (self._check_soloist(), True)[1])
        update.check(soup_transport(self.session), __version__, self._notify_update)

    # ------------------------------------------------------------------ ações
    def run_action(self, name: str) -> None:
        if self.win is None:
            return
        bar = self.win.placement == "waybar"
        if name == "bar-toggle":
            if self.state.ui_mode == "expanded":
                self.run_action("collapse")
            elif time.monotonic() - self._collapsed_at > BAR_REOPEN_GUARD_S:
                self.win.bar_point = hypr_cursor()
                self.run_action("expand")
        elif name == "toggle":
            if not self.win.get_visible() and not bar:
                self.win.set_visible(True)
            self.run_action("collapse" if self.state.ui_mode == "expanded" else "expand")
        elif name in ("volume-up", "volume-down"):
            step = VOLUME_STEP if name == "volume-up" else -VOLUME_STEP
            self.on_command("set_volume", {"volume": max(0, min(100, self.state.playback.volume + step))})
        elif name in ("expand", "expand-search", "expand-settings"):
            if not bar:
                self.win.set_visible(True)
            self._cancel_collapse()
            if name == "expand-search":
                self.state.set_ui(tab="search")
            elif name == "expand-settings":
                self.expanded.show_tab("settings")
            self.state.set_ui(mode="expanded")
            self.win.set_mode("expanded")
            self.focus.arm()
            if should_refresh_playlists(self._playlists_ok, time.monotonic(), bool(self.state.playlists)):
                self.refresh_playlists()
            self.queue_sync.request()
            if self.state.tab == "search":
                # o atalho de busca volta aos resultados; expandir à toa mantém a playlist aberta
                reset = name == "expand-search"
                GLib.idle_add(lambda: (self.expanded.focus_search(reset), False)[1])
            elif self.state.tab == "settings":
                self.expanded.settings.sync()
        elif name == "collapse":
            if self.state.ui_mode == "expanded":
                self._collapsed_at = time.monotonic()
            self._cancel_collapse()
            self.focus.disarm()
            self.state.set_ui(mode="compact")
            self.win.set_mode("compact")
        elif name == "hide":
            self.run_action("collapse")
            self.win.set_visible(False)
        elif name == "show":
            if not bar:
                self.win.set_visible(True)
        elif name == "hide-toggle":
            self.run_action("show" if not self.win.get_visible() else "hide")
        elif name == "play-pause":
            st = self.state.playback.status
            self.on_command("pause" if st in ("playing", "buffering") else "play", {})
        elif name == "next":
            self.on_command("skip_next", {})
        elif name == "prev":
            self.on_command("skip_prev", {})
        elif name == "quit":
            waybar.write_status(None)
            self.mpris.stop()
            self._cancel_collapse()
            self.focus.disarm()
            self.client.stop()
            self.win.destroy()
            self.release()
            self.quit()
        elif name == "click-anywhere":
            self.focus.on_click_anywhere()

    def _on_compact_key(self, keyval: int) -> bool:
        action = compact_key_action(keyval)
        if action:
            self.run_action(action)
            return True
        return False

    def _on_active_changed(self, win, _p) -> None:
        if self.state.ui_mode == "expanded" and not win.is_active():
            self._schedule_collapse()
        elif win.is_active():
            self._cancel_collapse()

    def _schedule_collapse(self) -> None:
        if self._collapse_id or self.state.ui_mode != "expanded":
            return

        def go():
            self._collapse_id = 0
            self.run_action("collapse")
            return False

        self._collapse_id = GLib.timeout_add(self.config.collapse_delay_ms, go)

    def _cancel_collapse(self) -> None:
        if self._collapse_id:
            GLib.source_remove(self._collapse_id)
            self._collapse_id = 0

    # ------------------------------------------------------------------ Soloist
    def _on_connection(self, _c, ok: bool) -> None:
        self.queue_sync.reset()
        if ok:
            self.queue_sync.request()
        self.state.set_playback(apply_event(self.state.playback, {"type": "_connected" if ok else "_disconnected"}))

    def _on_event(self, _c, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "queue_changed":
            tracks = self.queue_sync.on_queue_event(ev)
            if tracks is None:
                return
            ctx = self.state.playback.context_uri
            if ctx == self.state.queue_context and queue_is_truncated(self.state.queue, tracks):
                self.queue_sync.request()  # recorte de 10 do evento espontâneo: busca a fila inteira
                return
            self.state.set_queue(ctx, tracks)
            return
        if kind in ("track_changed", "context_changed", "playback_state") and self.state.ui_mode == "expanded":
            self.queue_sync.request()  # a fila só aparece no expandido; no compacto não precisa
        if kind == "error":
            self.queue_sync.reset()
            self._flash_error(ev.get("message") or "Erro no Soloist")
            return
        pb = apply_event(self.state.playback, ev)
        self.state.set_playback(replace(pb, track=fill_track(pb.track, self._meta)))
        skipped = self.skips.track_changed(self.state.playback.track, clock_ms())
        if skipped is not None:  # o Soloist não conseguiu tocar a faixa e passou para a próxima
            self._skipped.append(skipped.title)
            self._flash_error(skip_message(self._skipped), SKIP_NOTICE_MS)
        self._fetch_meta_if_missing()
        uri = self.state.playback.track.uri if self.state.playback.track else None
        if uri != self._last_track_uri:
            self._last_track_uri = uri
            self.state.set_liked(None)
            if uri and uri.startswith("spotify:track:"):
                self.api.is_liked(uri, lambda v, u=uri: self._last_track_uri == u and self.state.set_liked(v))

    def _fetch_meta_if_missing(self) -> None:
        t = self.state.playback.track
        if not t or t.title or not t.uri.startswith("spotify:track:") or t.uri in self._meta_pending:
            return
        self._meta_pending.add(t.uri)

        def got(full):
            self._meta_pending.discard(t.uri)
            if full is None:
                return
            self._meta[t.uri] = full
            pb = self.state.playback
            self.state.set_playback(replace(pb, track=fill_track(pb.track, self._meta)))

        self.api.track(t.uri, got)

    # ------------------------------------------------------------------ tela da playlist
    def _set_library(self, items: list) -> None:
        self._library = list(items)
        self.state.set_playlists(merge_playlists(self._library, self._played))

    def _remember_played(self, playlist) -> None:
        """Tocou uma playlist pela busca: ela passa a aparecer na aba Playlists."""
        played = remember_played(self._played, playlist)
        if played == self._played:
            return
        self._played = played
        try:
            save_playlists(PLAYED_PATH, played)
        except OSError:
            pass
        self.state.set_playlists(merge_playlists(self._library, played))

    def _open_playlist(self, playlist, tab: str) -> None:
        if tab == "search":
            self._remember_played(playlist)
        cmds = playlist_click_commands(self.state.playback, playlist.uri)
        for name, fields in cmds:
            self.on_command(name, fields)
        self.expanded.open_playlist(playlist, tab, starting=bool(cmds))
        self.queue_sync.request()

    def _play_playlist(self, uri: str, shuffle: bool) -> None:
        for name, fields in play_button_commands(uri, shuffle):
            self.on_command(name, fields)

    def _pick_track(self, context_uri: str, track_uri: str) -> None:
        """Toca a playlist a partir da música escolhida (o Soloist não tem esse comando; a Web API tem)."""
        self.skips.user_command(clock_ms())

        def done(err: str | None) -> None:
            if err is None:
                self.skips.user_command(clock_ms())  # a troca pode chegar depois de um pedido lento
                return
            n = skip_count(self.state.queue, track_uri) if context_uri.endswith(":collection") else None
            if n:  # Curtidas sem offset: avança até ela
                for _ in range(n):
                    self.on_command("skip_next", {})
            else:
                self._flash_error(err)

        self.api.play_at(self.state.playback.device_name, context_uri, track_uri, done)

    def on_command(self, name: str, fields: dict) -> None:
        if name != "set_volume":
            self.skips.user_command(clock_ms())  # troca de faixa pedida pelo usuário não é "pulo"
        pb = self.state.playback
        if not pb.connected:
            self._flash_error("Soloist desconectado")
            return
        if not pb.is_active:
            self.client.send("activate")
        if name == "set_repeat":
            for cmd, f in repeat_commands(fields["mode"]):
                self.client.send(cmd, **f)
            return
        if name == "set_volume":
            self.state.set_playback(optimistic(pb, name, **fields))
            v = self.volume.push(fields["volume"], clock_ms())
            if v is not None:
                self.client.send("set_volume", volume=v)
            if not self._vol_id:
                self._vol_id = GLib.timeout_add(VOLUME_FLUSH_MS, self._flush_volume)
            return
        self.state.set_playback(optimistic(pb, name, **fields))
        if not self.client.send(name, **fields):
            self.state.set_playback(pb)
            self._flash_error("Soloist desconectado")

    def _flush_volume(self) -> bool:
        self._vol_id = 0
        v = self.volume.flush(clock_ms())
        if v is not None:
            self.client.send("set_volume", volume=v)
        return False

    def _flash_error(self, msg: str, ms: int = ERROR_CLEAR_MS) -> None:
        self.state.set_error(msg)
        if self._error_id:
            GLib.source_remove(self._error_id)

        def clear():
            self._error_id = 0
            self._skipped = []
            self.state.set_error(None)
            return False

        self._error_id = GLib.timeout_add(ms, clear)

    # ------------------------------------------------------------------ Web API
    def refresh_playlists(self) -> None:
        if self._playlists_loading:
            return
        self._playlists_loading = True

        def got(items, err):
            if err:
                self._playlists_loading = False
                self.state.set_playlists_error(err)
                return
            save_playlists(CACHE_DIR / "playlists.json", items)

            def publish(liked):
                self._playlists_loading = False
                self._playlists_ok = time.monotonic()
                self.state.set_playlists_error(None)
                self._set_library(([liked] if liked else []) + items)

            def with_count(n):
                if not n:
                    publish(None)
                    return
                self.api.user_id(lambda uid: publish(liked_playlist(uid) if uid else None))

            self.api.liked_count(with_count)

        self.api.list_playlists(got)

    def on_search(self, q: str, offset: int) -> None:
        self._search_seq += 1
        seq = self._search_seq
        if not q:
            self.state.set_search("", [], [])
            return
        found: dict = {}  # playlists chegam junto com as músicas, só na 1ª página

        def got(items, err):
            if seq != self._search_seq:
                return
            if err:
                self._flash_error(err)
                self.state.set_search(q, self.state.search_results if offset else [], None if offset else [])
                return
            base = self.state.search_results if offset else []
            self.state.set_search(q, base + items, found.get("playlists"))

        self.api.search(q, offset, got,
                        on_playlists=(lambda pls: found.update(playlists=pls)) if offset == 0 else None)

    def on_like(self) -> None:
        pb = self.state.playback
        if not pb.track or self.state.liked is None:
            return
        uri, new = pb.track.uri, not self.state.liked
        self.state.set_liked(new)

        def done(ok):
            if not ok and self._last_track_uri == uri:
                self.state.set_liked(not new)
                self._flash_error("Não foi possível atualizar a curtida")

        self.api.set_liked(uri, new, done)

    # ------------------------------------------------------------------ Soloist build
    def _check_soloist(self) -> None:
        v = installed_version()
        self.state.set_soloist_days(days_left(v[1], date.today()) if v else None)

    # ------------------------------------------------------------------ Waybar
    def _tick_bar(self) -> bool:
        """A linha de progresso do item anda sozinha: o arquivo só muda a cada 1 %."""
        if self.config.placement == "waybar" and self.state.playback.status == "playing":
            self.update_bar()
        return True

    def update_bar(self) -> None:
        """O texto do item custom/hyprspot; flutuando, o item some."""
        if self.config.placement != "waybar":
            waybar.write_status(dict(waybar.HIDDEN))
            return
        st = self.state
        waybar.write_status(waybar.status(st.playback, st.ui_mode == "expanded", context_name(st),
                                          st.soloist_days_left))

    # ------------------------------------------------------------------ dicas
    def _hide_hint(self) -> None:
        """Quem empurra o widget para fora da tela provavelmente quer escondê-lo (1x por sessão)."""
        if self._hide_hint_shown:
            return
        self._hide_hint_shown = True
        n = Gio.Notification.new("Quer esconder o hyprspot?")
        n.set_body("Super+Alt+H esconde o widget sem parar a música. O mesmo atalho mostra de novo.")
        self.send_notification("hide-hint", n)

    # ------------------------------------------------------------------ versão nova
    def _notify_update(self, version: str, url: str) -> None:
        title, body = update.notification_text(version, __version__, str(Path(__file__).resolve().parent.parent))
        n = Gio.Notification.new(title)
        n.set_body(body)
        action = Gio.Action.print_detailed_name("app.open-url", GLib.Variant("s", url))
        n.set_default_action(action)
        n.add_button("Ver novidades", action)
        self.send_notification("update", n)


def run(argv: list[str]) -> int:
    return HyprspotApp().run(argv)
