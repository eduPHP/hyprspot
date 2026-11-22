"""Aba Ajustes: conversões puras e, com display, o comportamento dentro do expandido."""
import os

import pytest

from hyprspot.config import Config


def test_transparency_is_the_inverse_of_opacity():
    from hyprspot.ui.settings import opacity_from_pct, transparency_pct
    assert transparency_pct(0.94) == 6
    assert opacity_from_pct(6) == 0.94
    assert opacity_from_pct(100) == 0.3  # nunca some de vez
    assert opacity_from_pct(-5) == 1.0


def test_mask_key_hides_the_middle():
    from hyprspot.ui.settings import mask_key
    assert mask_key("spak_SHNbHtfVbyzA0hq") == "spak_••••A0hq"
    assert mask_key("curta") == "curta"


def test_opacity_css_keeps_the_old_look_at_the_default():
    from hyprspot.app import opacity_css
    css = opacity_css(0.94)
    assert ".hs-root { background-color: alpha(@hs_base, 0.94); }" in css
    assert ".hs-expanded { background-color: alpha(@hs_base, 0.97); }" in css
    assert ".hs-footer { background-color: alpha(@hs_bg, 0.60); }" in css


needs_display = pytest.mark.skipif(not os.environ.get("WAYLAND_DISPLAY"), reason="sem display")


class FakeActions:
    def __init__(self):
        self.cfg = Config(client_id="x", monitor="HDMI-A-1")
        self.calls = []

    def config(self):
        return self.cfg

    def preview_opacity(self, v):
        self.calls.append(("preview", v))

    def set_opacity(self, v):
        self.calls.append(("opacity", v))

    def set_anchor(self, a):
        self.cfg.anchor = a
        self.calls.append(("anchor", a))

    def set_monitor(self, n):
        self.cfg.monitor = n
        self.calls.append(("monitor", n))

    def monitors(self):
        return ["HDMI-A-1", "eDP-1"]

    def key_hint(self):
        return "spak_0123456789abcdef"

    def save_key(self, text):
        return None if text == "spak_nova" else "inválida"

    def open_key_page(self):
        pass

    def open_setup(self):
        pass


class NoArt:
    def load(self, url, cb):
        pass


@pytest.fixture
def expanded():
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.init()
    from hyprspot.state import AppState
    from hyprspot.ui.expanded import ExpandedView
    from hyprspot.ui.settings import SettingsView

    state, acts, quits = AppState(), FakeActions(), []
    settings = SettingsView(state, acts)
    v = ExpandedView(state, lambda *_: None, lambda *_: None, lambda: None, lambda: None, NoArt(),
                     on_quit=lambda: quits.append(1), settings=settings)
    return state, v, settings, acts, quits


@needs_display
def test_tab_cycles_through_three_tabs_and_hides_the_player(expanded):
    from gi.repository import Gdk
    state, v, *_ = expanded
    v._on_key(None, Gdk.KEY_Tab, 0, 0)
    v._on_key(None, Gdk.KEY_Tab, 0, 0)
    assert state.tab == "settings" and not v.footer.get_visible()
    v._on_key(None, Gdk.KEY_Tab, 0, 0)
    assert state.tab == "playlists" and v.footer.get_visible()
    v._on_key(None, Gdk.KEY_ISO_Left_Tab, 0, 0)
    assert state.tab == "settings"


@needs_display
def test_escape_on_settings_collapses():
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, Gtk
    Gtk.init()
    from hyprspot.state import AppState
    from hyprspot.ui.expanded import ExpandedView
    from hyprspot.ui.settings import SettingsView

    state, collapsed = AppState(), []
    v = ExpandedView(state, lambda *_: None, lambda *_: None, lambda: collapsed.append(1), lambda: None, NoArt(),
                     settings=SettingsView(state, FakeActions()))
    v.show_tab("settings")
    assert v._on_key(None, Gdk.KEY_Escape, 0, 0) is True
    assert collapsed == [1]


@needs_display
def test_settings_show_saved_values_and_pick_choices(expanded):
    state, v, s, acts, _ = expanded
    v.show_tab("settings")
    assert s.opacity.get_value() == 6
    assert list(s._monitor_btns) == ["", "HDMI-A-1", "eDP-1"]
    assert s._monitor_btns["HDMI-A-1"].has_css_class("on")
    assert s._anchor_btns["bottom-right"].has_css_class("on")
    assert s.key_current.get_label() == "spak_••••cdef"
    s._anchor_btns["top-left"].emit("clicked")
    s._monitor_btns["eDP-1"].emit("clicked")
    assert ("anchor", "top-left") in acts.calls and ("monitor", "eDP-1") in acts.calls
    assert s._anchor_btns["top-left"].has_css_class("on") and not s._anchor_btns["bottom-right"].has_css_class("on")
    assert s._monitor_btns["eDP-1"].has_css_class("on")
    s.opacity.set_value(40)
    assert acts.calls[-1] == ("preview", 0.6)  # gravar espera o controle parar (debounce)


@needs_display
def test_saving_the_key_reports_errors_and_clears_on_success(expanded):
    _, v, s, *_ = expanded
    s.key_entry.set_text("lixo")
    s._save_key()
    assert s.key_msg.get_label() == "inválida" and s.key_msg.has_css_class("hs-error")
    s.key_entry.set_text("spak_nova")
    s._save_key()
    assert s.key_entry.get_text() == "" and not s.key_msg.has_css_class("hs-error")
