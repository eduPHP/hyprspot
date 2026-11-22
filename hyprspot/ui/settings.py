"""Aba Ajustes do expandido: transparência, posição, chave do Soloist e conta."""
from __future__ import annotations

from typing import Callable, Protocol

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from hyprspot import __version__  # noqa: E402
from hyprspot.config import OPACITY_MAX, OPACITY_MIN, Config  # noqa: E402
from hyprspot.state import AppState  # noqa: E402
from hyprspot.ui.common import label, toggle_class  # noqa: E402

ANCHOR_LABELS = (("top-left", "↖  Cima, esquerda"), ("top-right", "↗  Cima, direita"),
                 ("bottom-left", "↙  Baixo, esquerda"), ("bottom-right", "↘  Baixo, direita"))
PLACEMENT_LABELS = (("floating", "Flutuando"), ("waybar", "Na Waybar"))
PLACEMENT_HINTS = {
    "floating": "Fica por cima das janelas, no canto que você escolher.",
    "waybar": "Vira um item na barra; o player abre embaixo dele. Botão direito toca ou pausa, rolar muda o volume.",
}
OPACITY_DEBOUNCE_MS = 300


class SettingsActions(Protocol):
    def config(self) -> Config: ...
    def preview_opacity(self, value: float) -> None: ...  # muda na tela, sem gravar
    def set_opacity(self, value: float) -> None: ...
    def set_blur(self, on: bool) -> None: ...
    def set_anchor(self, anchor: str) -> None: ...
    def set_placement(self, placement: str) -> None: ...
    def set_monitor(self, name: str) -> None: ...
    def monitors(self) -> list[str]: ...
    def key_hint(self) -> str | None: ...
    def save_key(self, text: str) -> str | None: ...  # mensagem de erro, ou None
    def open_key_page(self) -> None: ...
    def open_setup(self) -> None: ...


def transparency_pct(opacity: float) -> int:
    """O controle mostra transparência (0 % = opaco); o config guarda a opacidade."""
    return round((1 - opacity) * 100)


def opacity_from_pct(pct: float) -> float:
    return round(max(OPACITY_MIN, min(OPACITY_MAX, 1 - pct / 100)), 2)


def mask_key(key: str) -> str:
    return key if len(key) <= 10 else f"{key[:5]}••••{key[-4:]}"


def soloist_line(state: AppState) -> str:
    pb = state.playback
    parts = [f"Conectado como {pb.device_name}" if pb.connected else "Soloist desconectado"]
    days = state.soloist_days_left
    if days is not None:
        parts.append("build expirado" if days <= 0 else f"build vale mais {days} dias")
    return " · ".join(parts)


def _section(title: str) -> Gtk.Label:
    return label(title.upper(), "hs-section")


def _caption(text: str) -> Gtk.Label:
    lab = label(text, "hs-set-caption", ellipsize=False)
    lab.set_wrap(True)
    return lab


def _choice(text: str, on_click: Callable[[], None]) -> Gtk.Button:
    b = Gtk.Button(label=text)
    b.add_css_class("hs-choice")
    b.set_focus_on_click(False)
    b.connect("clicked", lambda *_: on_click())
    return b


def _pill(text: str, on_click: Callable[[], None], main: bool = False) -> Gtk.Button:
    b = Gtk.Button(label=text)
    b.add_css_class("hs-pill")
    if main:
        b.add_css_class("main")
    b.set_focus_on_click(False)
    b.connect("clicked", lambda *_: on_click())
    return b


class SettingsView(Gtk.Box):
    def __init__(self, state: AppState, actions: SettingsActions):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.state, self.a = state, actions
        self._opacity_id = 0
        self._anchor_btns: dict[str, Gtk.Button] = {}
        self._monitor_btns: dict[str, Gtk.Button] = {}

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                       margin_start=10, margin_end=10, margin_top=2, margin_bottom=12)
        body.add_css_class("hs-settings")
        for w in (*self._build_look(), *self._build_position(), *self._build_soloist(), *self._build_account()):
            body.append(w)
        sw = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.set_child(body)
        self.append(sw)

        state.connect("changed", self._on_changed)
        self.sync()

    # ------------------------------------------------------------------ seções
    def _build_look(self) -> list[Gtk.Widget]:
        row = Gtk.Box(spacing=8)
        row.append(label("Transparência", "hs-set-label"))
        self.opacity_value = label("", "hs-set-value")
        self.opacity_value.set_hexpand(True)
        self.opacity_value.set_xalign(1)
        row.append(self.opacity_value)
        top = transparency_pct(OPACITY_MIN)
        self.opacity = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, top, 1)
        self.opacity.set_draw_value(False)
        self.opacity.add_css_class("hs-scale")
        self.opacity.update_property([Gtk.AccessibleProperty.LABEL], ["Transparência do fundo"])
        self.opacity.connect("value-changed", self._on_opacity)
        blur_row = Gtk.Box(spacing=8)
        blur_row.append(label("Desfoque", "hs-set-label"))
        self.blur = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.blur.add_css_class("hs-switch")
        self.blur.update_property([Gtk.AccessibleProperty.LABEL], ["Desfoque atrás do widget"])
        self.blur.connect("notify::active", self._on_blur)
        blur_row.append(self.blur)
        return [_section("Aparência"), row, self.opacity,
                _caption("Só o fundo fica transparente; textos e botões continuam nítidos."),
                blur_row,
                _caption("Com o desfoque, o Hyprland também escurece o que fica atrás. "
                         "Desligado, a área de trabalho aparece nítida.")]

    def _build_position(self) -> list[Gtk.Widget]:
        grid = Gtk.Grid(column_spacing=6, row_spacing=6, column_homogeneous=True)
        for i, (anchor, text) in enumerate(ANCHOR_LABELS):
            b = _choice(text, lambda a=anchor: self._pick_anchor(a))
            self._anchor_btns[anchor] = b
            grid.attach(b, i % 2, i // 2, 1, 1)
        self.monitor_box = Gtk.Box(spacing=6, homogeneous=True)
        where = Gtk.Box(spacing=6, homogeneous=True)
        self._placement_btns: dict[str, Gtk.Button] = {}
        for key, text in PLACEMENT_LABELS:
            b = _choice(text, lambda k=key: self._pick_placement(k))
            self._placement_btns[key] = b
            where.append(b)
        self.placement_caption = _caption("")
        # só valem flutuando: somem na Waybar
        self._floating_only = [label("Canto", "hs-set-label"), grid,
                               _caption("Leva o widget para o canto escolhido. Depois, arrastar continua valendo."),
                               label("Monitor", "hs-set-label"), self.monitor_box,
                               _caption("Automático abre no monitor em foco.")]
        return [_section("Posição"), label("Onde fica", "hs-set-label"), where, self.placement_caption,
                *self._floating_only]

    def _build_soloist(self) -> list[Gtk.Widget]:
        self.soloist_status = label("", "hs-set-caption", ellipsize=False)
        self.soloist_status.set_wrap(True)
        key_row = Gtk.Box(spacing=8)
        key_row.append(label("Chave da API", "hs-set-label"))
        self.key_current = label("", "hs-set-value")
        self.key_current.add_css_class("hs-mono")
        self.key_current.set_hexpand(True)
        self.key_current.set_xalign(1)
        key_row.append(self.key_current)
        self.key_entry = Gtk.PasswordEntry(show_peek_icon=True, placeholder_text="Cole a nova chave")
        self.key_entry.add_css_class("hs-key")
        self.key_entry.update_property([Gtk.AccessibleProperty.LABEL], ["Nova chave do Soloist"])
        self.key_entry.connect("activate", lambda *_: self._save_key())
        btns = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        btns.append(_pill("Gerar chave", self.a.open_key_page))
        btns.append(_pill("Salvar e reiniciar", self._save_key, main=True))
        self.key_msg = label("", "hs-set-caption", ellipsize=False)
        self.key_msg.set_wrap(True)
        self.key_msg.set_visible(False)
        return [_section("Soloist"), self.soloist_status, key_row, self.key_entry, btns, self.key_msg]

    def _build_account(self) -> list[Gtk.Widget]:
        row = Gtk.Box(spacing=8)
        what = _caption("App da Web API, login no Spotify e pareamento.")
        what.set_hexpand(True)
        row.append(what)
        setup = _pill("Refazer configuração", self.a.open_setup)
        setup.set_valign(Gtk.Align.CENTER)
        row.append(setup)
        version = label(f"hyprspot {__version__}", "hs-set-caption")
        version.set_xalign(0.5)
        return [_section("Conta"), row, version]

    # ------------------------------------------------------------------ ações
    def _on_opacity(self, scale: Gtk.Scale) -> None:
        value = opacity_from_pct(scale.get_value())
        self.opacity_value.set_label(f"{transparency_pct(value)} %")
        if self._syncing:
            return
        self.a.preview_opacity(value)
        if self._opacity_id:
            GLib.source_remove(self._opacity_id)

        def save():
            self._opacity_id = 0
            self.a.set_opacity(value)
            return False

        self._opacity_id = GLib.timeout_add(OPACITY_DEBOUNCE_MS, save)

    def _on_blur(self, switch: Gtk.Switch, _p) -> None:
        if not self._syncing:
            self.a.set_blur(switch.get_active())

    def _pick_anchor(self, anchor: str) -> None:
        self.a.set_anchor(anchor)
        self.sync()

    def _pick_placement(self, placement: str) -> None:
        self.a.set_placement(placement)
        self.sync()

    def _pick_monitor(self, name: str) -> None:
        self.a.set_monitor(name)
        self.sync()

    def _save_key(self) -> None:
        err = self.a.save_key(self.key_entry.get_text())
        self.key_msg.set_visible(True)
        toggle_class(self.key_msg, "hs-error", bool(err))
        if err:
            self.key_msg.set_label(err)
            return
        self.key_entry.set_text("")
        self.key_msg.set_label("Chave salva. O Soloist está reiniciando e reconecta em alguns segundos.")
        self.sync()

    # ------------------------------------------------------------------ estado
    _syncing = False

    def sync(self) -> None:
        """Mostra o que está gravado (o arraste e outros processos também mudam o config)."""
        c = self.a.config()
        self._syncing = True
        self.opacity.set_value(transparency_pct(c.opacity))
        self.opacity_value.set_label(f"{transparency_pct(c.opacity)} %")
        self.blur.set_active(c.blur)
        self._syncing = False
        for key, b in self._placement_btns.items():
            toggle_class(b, "on", key == c.placement)
        self.placement_caption.set_label(PLACEMENT_HINTS[c.placement])
        for w in self._floating_only:
            w.set_visible(c.placement == "floating")
        for anchor, b in self._anchor_btns.items():
            toggle_class(b, "on", anchor == c.anchor)
        names = ["", *self.a.monitors()]
        if c.monitor and c.monitor not in names:
            names.append(c.monitor)  # gravado, mas desligado agora
        if list(self._monitor_btns) != names:
            while (child := self.monitor_box.get_first_child()) is not None:
                self.monitor_box.remove(child)
            self._monitor_btns = {}
            for n in names:
                b = _choice(n or "Automático", lambda n=n: self._pick_monitor(n))
                self._monitor_btns[n] = b
                self.monitor_box.append(b)
        for n, b in self._monitor_btns.items():
            toggle_class(b, "on", n == c.monitor)
        hint = self.a.key_hint()
        self.key_current.set_label(mask_key(hint) if hint else "nenhuma")
        self._refresh_soloist()

    def _refresh_soloist(self) -> None:
        self.soloist_status.set_label(soloist_line(self.state))

    def _on_changed(self, _s, what: str) -> None:
        if what in ("playback", "soloist"):
            self._refresh_soloist()
