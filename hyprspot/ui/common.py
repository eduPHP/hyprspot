"""Pequenos construtores de widgets compartilhados."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk, Pango  # noqa: E402

CSS_PATH = Path(__file__).with_name("style.css")


def load_css() -> None:
    from hyprspot.ui import theme
    theme.load(CSS_PATH)


# atalhos dos botões do player, mostrados na dica (com o mouse sobre o compacto, ou no expandido)
KEY_PREV, KEY_NEXT, KEY_PLAY = "←", "→", "Espaço"


def with_key(text: str, key: str) -> str:
    return f"{text} ({key})"


def icon_button(icon: str, label: str, on_click: Callable[[], None], *classes: str, size: int = 16) -> Gtk.Button:
    b = Gtk.Button()
    img = Gtk.Image.new_from_icon_name(icon)
    img.set_pixel_size(size)
    b.set_child(img)
    b.set_tooltip_text(label)
    b.update_property([Gtk.AccessibleProperty.LABEL], [label])
    b.set_focus_on_click(False)
    for c in classes or ("hs-btn",):
        b.add_css_class(c)
    b.connect("clicked", lambda *_: on_click())
    return b


def set_icon(button: Gtk.Button, icon: str) -> None:
    img = button.get_child()
    if isinstance(img, Gtk.Image) and img.get_icon_name() != icon:
        img.set_from_icon_name(icon)


def label(text: str = "", *classes: str, xalign: float = 0.0, ellipsize: bool = True) -> Gtk.Label:
    lb = Gtk.Label(label=text, xalign=xalign)
    if ellipsize:
        lb.set_ellipsize(Pango.EllipsizeMode.END)
        lb.set_single_line_mode(True)
        lb.set_max_width_chars(1)
        lb.set_hexpand(True)
    for c in classes:
        lb.add_css_class(c)
    return lb


def toggle_class(w: Gtk.Widget, cls: str, on: bool) -> None:
    (w.add_css_class if on else w.remove_css_class)(cls)
