"""Modo expandido: abas Playlists | Buscar, lista rolável e rodapé com controles."""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from hyprspot.presenter import fmt_time, now_playing  # noqa: E402
from hyprspot.state import AppState, clock_ms, position_now  # noqa: E402
from hyprspot.ui.common import (KEY_NEXT, KEY_PLAY, KEY_PREV, icon_button, label, set_icon,  # noqa: E402
                                toggle_class, with_key)
from hyprspot.ui.compact import context_name  # noqa: E402
from hyprspot.ui.cover import ArtLoader, Cover  # noqa: E402
from hyprspot.ui.lists import make_list, playlist_row, replace_rows, track_row  # noqa: E402
from hyprspot.ui.playlist_page import PlaylistPage  # noqa: E402
from hyprspot.ui import theme  # noqa: E402
from hyprspot.model import Playlist  # noqa: E402

OnCommand = Callable[[str, dict], None]
SEARCH_DEBOUNCE_MS = 250
SEEK_DEBOUNCE_MS = 150
USER_DRAG_HOLD_MS = 600
SEARCH_PLAYLISTS = 5
REPEAT_NEXT = {"off": "context", "context": "track", "track": "off"}


class ExpandedView(Gtk.Box):
    WIDTH, HEIGHT = 360, 600

    def __init__(self, state: AppState, on_command: OnCommand, on_search: Callable[[str, int], None],
                 on_collapse: Callable[[], None], on_like: Callable[[], None], art: ArtLoader,
                 on_quit: Callable[[], None] | None = None,
                 on_open_playlist: Callable[[Playlist, str], None] | None = None,
                 on_play_playlist: Callable[[str, bool], None] | None = None,
                 on_pick_track: Callable[[str, str], None] | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.state, self.on_command, self.on_search, self.on_like = state, on_command, on_search, on_like
        self.on_collapse = on_collapse
        self.on_quit = on_quit or (lambda: None)
        self.on_open_playlist = on_open_playlist or (lambda p, tab: self.on_command("play", {"uri": p.uri}))
        self.on_play_playlist = on_play_playlist or (lambda uri, shuffle: None)
        self.on_pick_track = on_pick_track or (lambda ctx, uri: None)
        self.art = art
        self.set_size_request(self.WIDTH, self.HEIGHT)
        self.add_css_class("hs-root")
        self.add_css_class("hs-expanded")
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self._seek_id = 0
        self._user_drag_until = 0
        self._loading_more = False
        self._search_offset = 0

        self.append(self._build_header())
        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.NONE)
        # cada aba tem a sua pilha: a lista e, por cima, a tela de uma playlist
        self.navs: dict[str, Gtk.Stack] = {}
        self.pages: dict[str, PlaylistPage] = {}
        self.stack.add_named(self._nav("playlists", self._build_playlists()), "playlists")
        self.stack.add_named(self._nav("search", self._build_search()), "search")
        self.append(self.stack)
        self.append(self._build_footer())

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        state.connect("changed", self._on_changed)
        self._refresh_tabs()
        self._refresh_playlists()
        self._refresh_search()
        self._refresh_playback()
        GLib.timeout_add(250, self._tick)

    # ------------------------------------------------------------------ build
    def _build_header(self) -> Gtk.Widget:
        bar = Gtk.Box(spacing=6, margin_top=10, margin_start=10, margin_end=10, margin_bottom=8)
        tabs = Gtk.Box(spacing=4, hexpand=True, homogeneous=True)
        tabs.add_css_class("hs-tabs")
        self.tab_pl = self._tab("Playlists", "view-list-bullet-symbolic", "playlists")
        self.tab_sr = self._tab("Buscar", "system-search-symbolic", "search")
        tabs.append(self.tab_pl)
        tabs.append(self.tab_sr)
        bar.append(tabs)
        bar.append(icon_button("pan-down-symbolic", "Recolher", self.on_collapse))
        return bar

    def _tab(self, text: str, icon: str, name: str) -> Gtk.Button:
        b = Gtk.Button()
        b.add_css_class("hs-tab")
        b.set_focus_on_click(False)
        inner = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        img = Gtk.Image.new_from_icon_name(icon)
        img.set_pixel_size(14)
        inner.append(img)
        inner.append(Gtk.Label(label=text))
        b.set_child(inner)
        b.connect("clicked", lambda *_: self.show_tab(name))
        return b

    def _scrolled(self, child: Gtk.Widget) -> Gtk.ScrolledWindow:
        sw = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.set_child(child)
        return sw

    def _nav(self, tab: str, root: Gtk.Widget) -> Gtk.Stack:
        nav = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE)
        nav.add_named(root, "list")
        page = PlaylistPage(self.art, on_back=lambda: self.close_playlist(tab),
                            on_play=lambda shuffle: self.on_play_playlist(page.playlist.uri, shuffle),
                            on_pick=lambda uri: self.on_pick_track(page.playlist.uri, uri))
        nav.add_named(page, "page")
        self.navs[tab], self.pages[tab] = nav, page
        return nav

    def _playlist_by_uri(self, uri: str) -> Playlist | None:
        return next((p for p in self.state.playlists if p.uri == uri), None)

    def _build_playlists(self) -> Gtk.Widget:
        self.pl_list = make_list()
        self.pl_list.connect("row-activated", lambda _l, row: self._activate_playlist(row.uri, "playlists"))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=6, margin_end=6)
        self.pl_hint = label("Carregando playlists…", "hs-hint", ellipsize=False)
        self.pl_hint.set_wrap(True)
        box.append(self.pl_hint)
        box.append(self._scrolled(self.pl_list))
        return box

    def _build_search(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=10, margin_end=10)
        self.entry = Gtk.SearchEntry(placeholder_text="Buscar músicas ou playlists")
        self.entry.add_css_class("hs-search")
        self.entry.set_search_delay(SEARCH_DEBOUNCE_MS)
        self.entry.update_property([Gtk.AccessibleProperty.LABEL], ["Buscar músicas ou playlists"])
        self.entry.connect("search-changed", self._on_search_changed)
        self.entry.connect("activate", lambda *_: self._play_selected_or_first())
        ek = Gtk.EventControllerKey()
        ek.connect("key-pressed", self._on_entry_key)
        self.entry.add_controller(ek)
        box.append(self.entry)
        self.sr_hint = label("", "hs-hint", ellipsize=False)
        self.sr_hint.set_wrap(True)
        self.sr_hint.set_use_markup(True)
        box.append(self.sr_hint)
        # playlists (até 5) acima das músicas, como no celular; Enter e ↑↓ continuam só nas músicas
        results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sr_pl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sr_pl_box.append(label("Playlists", "hs-section"))
        self.sr_pl_list = make_list()
        self.sr_pl_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.sr_pl_list.connect("row-activated", lambda _l, row: self._activate_search_playlist(row.uri))
        self.sr_pl_box.append(self.sr_pl_list)
        self.sr_tr_label = label("Músicas", "hs-section")
        results.append(self.sr_pl_box)
        results.append(self.sr_tr_label)
        self.sr_list = make_list()
        self.sr_list.connect("row-activated", lambda _l, row: self.on_command("play", {"uri": row.uri}))
        results.append(self.sr_list)
        self._sr_results = results
        self.sr_scroll = self._scrolled(results)
        self.sr_scroll.get_vadjustment().connect("value-changed", self._maybe_load_more)
        box.append(self.sr_scroll)
        return box

    def _build_footer(self) -> Gtk.Widget:
        f = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        f.add_css_class("hs-footer")

        top = Gtk.Box(spacing=12)
        self.cover = Cover(52, self.art, "big")
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER, hexpand=True)
        self.title = label("", "hs-title")
        self.detail = label("", "hs-artist")
        texts.append(self.title)
        texts.append(self.detail)
        self.like = icon_button("emblem-favorite-symbolic", "Curtir", self.on_like, size=17)
        top.append(self.cover)
        top.append(texts)
        top.append(self.like)
        f.append(top)

        prog = Gtk.Box(spacing=8)
        self.t_now = Gtk.Label(label="0:00", width_chars=5, xalign=1)
        self.t_now.add_css_class("hs-mono")
        self.seek = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 1000)
        self.seek.set_draw_value(False)
        self.seek.set_hexpand(True)
        self.seek.add_css_class("hs-scale")
        self.seek.update_property([Gtk.AccessibleProperty.LABEL], ["Posição da faixa"])
        self.seek.connect("change-value", self._on_seek)
        self.t_total = Gtk.Label(label="0:00", width_chars=5, xalign=0)
        self.t_total.add_css_class("hs-mono")
        for w in (self.t_now, self.seek, self.t_total):
            prog.append(w)
        f.append(prog)

        ctr = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        self.shuffle = icon_button("media-playlist-shuffle-symbolic", "Aleatório",
                                   lambda: self.on_command("set_shuffle", {"enabled": not self.state.playback.shuffle}))
        self.prev = icon_button("media-skip-backward-symbolic", with_key("Faixa anterior", KEY_PREV),
                                lambda: self.on_command("skip_prev", {}), size=18)
        self.play = icon_button("media-playback-start-symbolic", with_key("Tocar", KEY_PLAY), self._toggle_play,
                                "hs-play", "big", size=18)
        self.next = icon_button("media-skip-forward-symbolic", with_key("Próxima faixa", KEY_NEXT),
                                lambda: self.on_command("skip_next", {}), size=18)
        self.repeat = icon_button("media-playlist-repeat-symbolic", "Repetir",
                                  lambda: self.on_command("set_repeat", {"mode": REPEAT_NEXT[self.state.playback.repeat]}))
        for w in (self.shuffle, self.prev, self.play, self.next, self.repeat):
            ctr.append(w)
        f.append(ctr)

        bottom = Gtk.Box(spacing=10)
        dev = Gtk.Box(spacing=6, hexpand=True)
        dev_icon = Gtk.Image.new_from_icon_name("computer-symbolic")
        dev_icon.add_css_class("hs-eq")
        self.device = label("", "hs-mono")
        dev.append(dev_icon)
        dev.append(self.device)
        bottom.append(dev)
        vol = Gtk.Box(spacing=4)
        vol_icon = Gtk.Image.new_from_icon_name("audio-volume-high-symbolic")
        vol_icon.set_pixel_size(14)
        self.volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        self.volume.set_draw_value(False)
        self.volume.set_size_request(78, -1)
        self.volume.add_css_class("hs-scale")
        self.volume.add_css_class("volume")
        self.volume.update_property([Gtk.AccessibleProperty.LABEL], ["Volume"])
        self.volume.connect("change-value", self._on_volume)
        vol.append(vol_icon)
        vol.append(self.volume)
        bottom.append(vol)
        f.append(bottom)

        self.warn = label("", "hs-mono", ellipsize=False)
        self.warn.add_css_class("hs-warn")
        self.warn.set_wrap(True)
        self.warn.set_visible(False)
        f.append(self.warn)
        return f

    # ------------------------------------------------------------------ tela da playlist
    def _activate_playlist(self, uri: str, tab: str) -> None:
        p = self._playlist_by_uri(uri)
        if p is not None:
            self.on_open_playlist(p, tab)

    def _activate_search_playlist(self, uri: str) -> None:
        p = next((p for p in self.state.search_playlists if p.uri == uri), None)
        if p is not None:
            self.on_open_playlist(p, "search")

    def open_playlist(self, playlist: Playlist, tab: str, starting: bool = False) -> None:
        page = self.pages[tab]
        page.show(playlist, "Playlists" if tab == "playlists" else "Buscar", starting)
        self.navs[tab].set_visible_child_name("page")
        self._refresh_pages()

    def close_playlist(self, tab: str | None = None) -> None:
        self.navs[tab or self.state.tab].set_visible_child_name("list")

    def page_open(self, tab: str) -> bool:
        return self.navs[tab].get_visible_child_name() == "page"

    def _refresh_pages(self) -> None:
        for tab, page in self.pages.items():
            if self.page_open(tab):
                pb = self.state.playback
                page.set_queue(self.state.queue_context, self.state.queue, pb.track, pb.context_uri)

    # ------------------------------------------------------------------ ações
    def show_tab(self, name: str) -> None:
        self.state.set_ui(tab=name)
        if name == "search":
            self.focus_search(reset=False)

    def focus_search(self, reset: bool = True) -> None:
        """`reset`: `/` e o atalho de busca voltam aos resultados; trocar de aba mantém a playlist aberta."""
        if reset:
            self.close_playlist("search")
        self.stack.set_visible_child_name("search")
        if not self.page_open("search"):
            self.entry.grab_focus()

    def _toggle_play(self) -> None:
        self.on_command("pause" if self.state.playback.status in ("playing", "buffering") else "play", {})

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        q = entry.get_text().strip()
        self._search_offset = 0
        self._loading_more = False
        self.on_search(q, 0)

    def _maybe_load_more(self, adj: Gtk.Adjustment) -> None:
        q = self.state.search_query
        n = len(self.state.search_results)
        if not q or self._loading_more or n == 0 or n % 10 != 0:
            return
        if adj.get_value() + adj.get_page_size() >= adj.get_upper() - 46:
            self._loading_more = True
            self._search_offset = n
            self.on_search(q, n)

    def _play_selected_or_first(self) -> None:
        row = self.sr_list.get_selected_row() or self.sr_list.get_row_at_index(0)
        if row is not None:
            self.on_command("play", {"uri": row.uri})

    def _on_entry_key(self, _c, keyval, _code, _state) -> bool:
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            row = self.sr_list.get_selected_row()
            idx = row.get_index() if row else -1
            nxt = self.sr_list.get_row_at_index(idx + (1 if keyval == Gdk.KEY_Down else -1))
            if nxt is not None:
                self.sr_list.select_row(nxt)
                self._scroll_to(nxt)
            return True
        return False

    def _scroll_to(self, row: Gtk.ListBoxRow) -> None:
        adj = self.sr_scroll.get_vadjustment()
        pt = row.translate_coordinates(self._sr_results, 0, 0)  # a seção de playlists fica acima
        y = pt[1] if pt else row.get_index() * 46
        if y < adj.get_value():
            adj.set_value(y)
        elif y + 46 > adj.get_value() + adj.get_page_size():
            adj.set_value(y + 46 - adj.get_page_size())

    def _on_key(self, _c, keyval, _code, mods) -> bool:
        typing = self.entry.has_focus() or (self.entry.get_focus_child() is not None)
        if keyval == Gdk.KEY_Escape:
            if self.page_open(self.state.tab):  # Esc volta da tela da playlist antes de recolher
                self.close_playlist()
            else:
                self.on_collapse()
            return True
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            self.show_tab("search" if self.state.tab == "playlists" else "playlists")
            return True
        if keyval in (Gdk.KEY_q, Gdk.KEY_Q) and not typing:
            self.on_quit()
            return True
        if keyval == Gdk.KEY_slash and not typing:
            self.state.set_ui(tab="search")
            self.focus_search()
            return True
        if keyval == Gdk.KEY_space and not typing:
            self._toggle_play()
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Right) and not typing:
            self.on_command("skip_prev" if keyval == Gdk.KEY_Left else "skip_next", {})
            return True
        return False

    def _on_seek(self, _scale, _scroll, value) -> bool:
        self._user_drag_until = clock_ms() + USER_DRAG_HOLD_MS
        if self._seek_id:
            GLib.source_remove(self._seek_id)

        def send():
            self._seek_id = 0
            self.on_command("seek", {"position_ms": int(max(0, value))})
            return False

        self._seek_id = GLib.timeout_add(SEEK_DEBOUNCE_MS, send)
        return False

    def _on_volume(self, _scale, _scroll, value) -> bool:
        self._user_drag_until = clock_ms() + USER_DRAG_HOLD_MS
        self.on_command("set_volume", {"volume": int(max(0, min(100, value)))})
        return False

    # ------------------------------------------------------------------ estado
    def _on_changed(self, _s, what: str) -> None:
        if what == "ui":
            self._refresh_tabs()
        elif what == "playlists":
            self._refresh_playlists()
            self._refresh_playback()
        elif what == "search":
            self._refresh_search()
        elif what == "error":
            self._refresh_search()
            self._refresh_playback()
        elif what in ("playback", "liked", "soloist"):
            self._refresh_playback()
            self._mark_active()
            if what == "playback":
                self._refresh_pages()
        elif what == "queue":
            self._refresh_pages()

    def _refresh_tabs(self) -> None:
        tab = self.state.tab
        toggle_class(self.tab_pl, "selected", tab == "playlists")
        toggle_class(self.tab_sr, "selected", tab == "search")
        self.stack.set_visible_child_name(tab)

    def _refresh_playlists(self) -> None:
        items = self.state.playlists
        replace_rows(self.pl_list, [playlist_row(p, self.art) for p in items])
        err = self.state.playlists_error
        self.pl_hint.set_visible(not items)
        self.pl_hint.set_label(err or "Carregando playlists…")
        toggle_class(self.pl_hint, "hs-error", bool(err))
        self._mark_active()

    def _refresh_search(self) -> None:
        q, results = self.state.search_query, self.state.search_results
        pls = self.state.search_playlists[:SEARCH_PLAYLISTS] if q else []
        self._loading_more = False
        replace_rows(self.sr_pl_list, [playlist_row(p, self.art) for p in pls])
        self.sr_pl_box.set_visible(bool(pls))
        self.sr_tr_label.set_visible(bool(pls) and bool(results))
        replace_rows(self.sr_list, [track_row(t, self.art) for t in results])
        if not q:
            msg = f"Digite para buscar. <span font_family='JetBrains Mono' foreground='{theme.hex('hs_fg')}'>Enter</span> toca o primeiro resultado."
        elif self.state.error:
            msg = f"<span foreground='{theme.hex('hs_red')}'>{GLib.markup_escape_text(self.state.error)}</span>"
        elif not results and not pls:
            msg = f"Nada encontrado para “{GLib.markup_escape_text(q)}”."
        else:
            msg = ""
        self.sr_hint.set_markup(msg)
        self.sr_hint.set_visible(bool(msg))
        if results and self.sr_list.get_selected_row() is None:
            self.sr_list.select_row(self.sr_list.get_row_at_index(0))
        self._mark_active()

    def _mark_active(self) -> None:
        pb = self.state.playback
        for lb, key in ((self.pl_list, pb.context_uri), (self.sr_pl_list, pb.context_uri),
                        (self.sr_list, pb.track.uri if pb.track else None)):
            row = lb.get_first_child()
            while row is not None:
                row.set_active(bool(key) and row.uri == key)
                row = row.get_next_sibling()

    def _refresh_playback(self) -> None:
        pb = self.state.playback
        v = now_playing(pb, context_name(self.state))
        self.title.set_label(v.title)
        self.detail.set_label(v.detail or v.subtitle)
        self.cover.set_url(v.art_url)
        has_track = v.enabled and pb.track is not None
        self.play.set_sensitive(v.enabled)
        for w in (self.prev, self.next, self.shuffle, self.repeat, self.seek, self.volume):
            w.set_sensitive(has_track)
        set_icon(self.play, "media-playback-pause-symbolic" if v.playing else "media-playback-start-symbolic")
        self.play.set_tooltip_text(with_key("Pausar" if v.playing else "Tocar", KEY_PLAY))
        toggle_class(self.shuffle, "on", pb.shuffle)
        toggle_class(self.repeat, "on", pb.repeat != "off")
        set_icon(self.repeat, "media-playlist-repeat-song-symbolic" if pb.repeat == "track" else "media-playlist-repeat-symbolic")
        liked = self.state.liked
        self.like.set_visible(pb.track is not None and liked is not None)
        toggle_class(self.like, "liked", bool(liked))
        set_icon(self.like, "heart-filled-symbolic" if liked else "emblem-favorite-symbolic")
        self.like.set_tooltip_text("Remover das curtidas" if liked else "Curtir")
        self.device.set_label(f"{pb.device_name} · este computador" if pb.connected else "Soloist desconectado")
        days = self.state.soloist_days_left
        if days is not None and days <= 14:
            self.warn.set_label("Soloist expirou: rode hyprspot soloist-update" if days <= 0
                                else f"Soloist expira em {days} dias: rode hyprspot soloist-update")
            self.warn.set_visible(True)
        else:
            self.warn.set_visible(False)
        if clock_ms() > self._user_drag_until:
            self.volume.set_value(pb.volume)
        self._tick()

    def _tick(self) -> bool:
        if not self.get_visible():
            return True
        pb = self.state.playback
        dur = pb.track.duration_ms if pb.track else 0
        pos = position_now(pb, clock_ms())
        self.t_now.set_label(fmt_time(pos))
        self.t_total.set_label(fmt_time(dur))
        if clock_ms() > self._user_drag_until:
            self.seek.set_range(0, max(dur, 1))
            self.seek.set_value(pos)
        return True
