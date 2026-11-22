"""Foco de teclado do compacto ao passar o mouse (precisa de display Wayland)."""
import os
import types

import pytest

from hyprspot.focus import Output

pytestmark = pytest.mark.skipif(not os.environ.get("WAYLAND_DISPLAY"), reason="sem display")


class FakeLS:
    """Substitui o Gtk4LayerShell: registra os modos de teclado pedidos."""
    Edge = types.SimpleNamespace(TOP="top", BOTTOM="bottom", LEFT="left", RIGHT="right")
    Layer = types.SimpleNamespace(OVERLAY="overlay")
    KeyboardMode = types.SimpleNamespace(NONE="none", EXCLUSIVE="exclusive", ON_DEMAND="on_demand")

    def __init__(self):
        self.modes = []
        self.margins = {}

    def set_keyboard_mode(self, _win, mode):
        self.modes.append(mode)

    def set_margin(self, _win, edge, value):
        self.margins[edge] = value

    def __getattr__(self, _name):
        return lambda *a, **k: None


@pytest.fixture
def win(monkeypatch):
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.init()
    from hyprspot.config import Config
    from hyprspot.ui import window as W

    ls = FakeLS()
    monkeypatch.setattr(W, "LS", ls)
    monkeypatch.setattr(W, "EDGES", {"top": "top", "bottom": "bottom", "left": "left", "right": "right"})
    compact = Gtk.Box()
    compact.set_size_request(320, 64)
    cursor = {"pos": None, "outs": []}
    w = W.HyprspotWindow(None, Config(client_id=""), compact, None, lambda m: None,
                         cursor_pos=lambda: cursor["pos"], outputs=lambda: cursor["outs"])
    w.fake_cursor = cursor
    w.bound = []
    w._bind_output = lambda name: (w.bound.append(name), setattr(w, "output", name), True)[2]
    w.get_width = lambda: 320
    w.get_height = lambda: 64
    yield w, ls
    w.destroy()


def test_hover_grab_is_released_when_pointer_moves_outside(win):
    # Com o teclado exclusivo, o Hyprland segue mandando o movimento para o widget e
    # não manda "leave": o widget prendia teclado e mouse depois que o cursor saía.
    w, ls = win
    w._grab_keyboard()
    assert ls.modes[-1] == "exclusive"
    w._on_motion(None, 296, 30)                   # ainda dentro
    assert ls.modes[-1] == "exclusive"
    w._on_motion(None, 296, -4)                   # saiu por cima
    assert ls.modes[-1] == "none"


@pytest.mark.parametrize("x,y", [(-1, 30), (320, 30), (100, 64)])
def test_any_edge_releases_the_grab(win, x, y):
    w, ls = win
    w._grab_keyboard()
    w._on_motion(None, x, y)
    assert ls.modes[-1] == "none"


def test_motion_outside_in_expanded_mode_keeps_keyboard(win):
    w, ls = win
    w.mode = "expanded"
    n = len(ls.modes)
    w._on_motion(None, 10, -50)
    assert len(ls.modes) == n


def test_motion_inside_after_edge_event_still_schedules_the_grab(win):
    # Ao entrar, o primeiro movimento pode chegar com y = -1; o seguinte, já dentro,
    # precisa agendar a captura do teclado de novo.
    w, ls = win
    w._hover_enter()
    w._on_motion(None, 296, -1)
    assert w._hover_id == 0
    w._on_motion(None, 296, 5)
    assert w._hover_id != 0


def test_motion_outside_without_grab_does_nothing(win):
    w, ls = win
    n = len(ls.modes)
    w._on_motion(None, 296, -30)
    assert len(ls.modes) == n


ONE = Output("eDP-1", 0, 0, 1600, 874)


class FakeGesture:
    def __init__(self):
        self.states = []

    def set_state(self, state):
        self.states.append(state)


def test_drag_margin_follows_the_anchor():
    from hyprspot.ui.window import drag_margin
    # ancorado embaixo à direita: arrastar para a esquerda/cima aumenta a margem
    assert drag_margin((16, 16), -200, -50, right=True, bottom=True) == (216, 66)
    # ancorado em cima à esquerda: arrastar para a direita/baixo aumenta a margem
    assert drag_margin((16, 16), 200, 50, right=False, bottom=False) == (216, 66)
    assert drag_margin((16, 16), 500, 500, right=True, bottom=True) == (0, 0)   # nunca negativa


def test_left_drag_follows_the_cursor_live(win):
    # touchpad: arrastar com o botão esquerdo (um dedo) move o widget; o botão do meio é difícil.
    # A posição vem do cursor na tela (Hyprland), não do evento do GTK, que muda quando a
    # janela anda: assim o widget acompanha sem tremer e para exatamente onde soltou.
    w, _ls = win
    saved = []
    w._on_margin_changed = saved.append
    w.margin = (16, 16)
    w._horiz, w._vert = "right", "bottom"
    g = FakeGesture()
    w.fake_cursor["pos"] = (1400.0, 850.0)
    w._drag_begin(g, 0, 0, left=True)
    w.fake_cursor["pos"] = (1390.0, 845.0)
    w._drag_update(g, -10, -5, left=True)           # passou do limite: virou arrasto
    assert g.states and w.has_css_class("hs-dragging")
    w.fake_cursor["pos"] = (1300.0, 830.0)
    w._drag_tick()
    assert w.margin == (116, 36)                     # já andou, antes de soltar
    w.fake_cursor["pos"] = (1250.0, 700.0)
    w._drag_update(g, 999, 999, left=True)          # o deslocamento do GTK é ignorado
    w._drag_end(g, 999, 999, left=True)
    assert w.margin == (166, 166) and saved == [(166, 166)]
    assert not w.has_css_class("hs-dragging")


def test_left_drag_without_hyprland_moves_on_release(win):
    w, _ls = win
    saved = []
    w._on_margin_changed = saved.append
    w.margin = w._drag_start = (16, 16)
    w._horiz, w._vert = "right", "bottom"
    g = FakeGesture()                            # cursor_pos devolve None: sem socket do Hyprland
    w._drag_begin(g, 0, 0, left=True)
    w._drag_update(g, -100, -20, left=True)
    assert w.margin == (16, 16)                  # sem a posição na tela, só move ao soltar
    assert g.states and w.has_css_class("hs-dragging")
    w._drag_end(g, -100, -20, left=True)
    assert w.margin == (116, 36) and saved == [(116, 36)]
    assert not w.has_css_class("hs-dragging")


def test_small_left_motion_is_still_a_click(win):
    w, _ls = win
    saved = []
    w._on_margin_changed = saved.append
    w.margin = w._drag_start = (16, 16)
    g = FakeGesture()
    w._drag_begin(g, 0, 0, left=True)
    w._drag_update(g, 3, 2, left=True)
    w._drag_end(g, 3, 2, left=True)
    assert g.states == [] and w.margin == (16, 16) and saved == []


def test_left_drag_is_ignored_in_expanded_mode(win):
    # no expandido o botão esquerdo é das listas, do progresso e do volume
    w, _ls = win
    w.mode = "expanded"
    w.margin = w._drag_start = (16, 16)
    g = FakeGesture()
    w._drag_begin(g, 0, 0, left=True)
    w._drag_update(g, -100, 0, left=True)
    w._drag_end(g, -100, 0, left=True)
    assert g.states == [] and w.margin == (16, 16)


def test_middle_drag_works_in_both_modes(win):
    w, _ls = win
    w.mode = "expanded"
    w.margin = w._drag_start = (16, 16)
    w._horiz, w._vert = "right", "bottom"
    g = FakeGesture()
    w._drag_begin(g, 0, 0, left=False)
    w._drag_update(g, -40, 0, left=False)
    w._drag_end(g, -40, 0, left=False)
    assert w.margin == (56, 16)


def test_clamp_margin_keeps_widget_inside_area():
    from hyprspot.ui.window import clamp_margin
    area, size = (1600, 874), (320, 64)
    assert clamp_margin((100, 100), size, area) == (100, 100)
    assert clamp_margin((5000, 5000), size, area) == (1280, 810)
    assert clamp_margin((-20, 900), size, area) == (0, 810)
    assert clamp_margin((50, 50), (2000, 1000), area) == (0, 0)   # maior que a tela: encosta na borda


def test_drag_off_screen_stops_at_the_nearest_visible_spot(win):
    # arrastar para fora da tela: o widget para na borda e é ali que fica salvo
    w, _ls = win
    saved = []
    w._on_margin_changed = saved.append
    w.margin = (16, 16)
    w._horiz, w._vert = "right", "bottom"
    w.fake_cursor["outs"] = [ONE]
    g = FakeGesture()
    w.fake_cursor["pos"] = (1400.0, 850.0)
    w._drag_begin(g, 0, 0, left=True)
    w.fake_cursor["pos"] = (-300.0, -200.0)          # bem além do canto de cima à esquerda
    w._drag_update(g, -50, -50, left=True)
    w._drag_tick()
    assert w.margin == (1280, 810)                   # 1600−320, 874−64
    w._drag_end(g, 0, 0, left=True)
    assert saved == [(1280, 810)]


def _drag_to(w, pos):
    g = FakeGesture()
    w.fake_cursor["pos"] = (1400.0, 850.0)
    w._drag_begin(g, 0, 0, left=True)
    w.fake_cursor["pos"] = pos
    w._drag_update(g, -50, -50, left=True)
    w._drag_end(g, 0, 0, left=True)


def test_pushing_off_screen_triggers_the_hide_hint(win):
    # quem tenta tirar o widget da tela provavelmente quer escondê-lo: o app avisa do atalho
    w, _ls = win
    hints = []
    w._on_push_offscreen = lambda: hints.append(1)
    w.margin = (16, 16)
    w._horiz, w._vert = "right", "bottom"
    w.fake_cursor["outs"] = [ONE]
    _drag_to(w, (1400.0 + 200, 850.0))              # 200 px além da borda direita
    assert hints == [1]


def test_reaching_the_edge_is_not_a_push(win):
    w, _ls = win
    hints = []
    w._on_push_offscreen = lambda: hints.append(1)
    w.margin = (16, 16)
    w._horiz, w._vert = "right", "bottom"
    w.fake_cursor["outs"] = [ONE]
    _drag_to(w, (1400.0 + 40, 850.0))               # encosta na borda e passa só 24 px
    assert hints == [] and w.margin == (0, 16)


def test_expanding_near_the_top_stays_inside_the_screen(monkeypatch):
    # compacto encostado no topo: o expandido (bem mais alto) desce o quanto precisar para caber;
    # ao recolher, o compacto volta para a posição que o usuário escolheu
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.init()
    from hyprspot.config import Config
    from hyprspot.ui import window as W

    ls = FakeLS()
    monkeypatch.setattr(W, "LS", ls)
    monkeypatch.setattr(W, "EDGES", {"top": "top", "bottom": "bottom", "left": "left", "right": "right"})
    compact, expanded = Gtk.Box(), Gtk.Box()
    compact.set_size_request(320, 64)
    expanded.set_size_request(360, 520)
    w = W.HyprspotWindow(None, Config(client_id="", anchor="bottom-right", margin=(16, 800)), compact, expanded,
                         lambda m: None, cursor_pos=lambda: (1400.0, 50.0), outputs=lambda: [ONE])
    try:
        assert ls.margins == {"right": 16, "bottom": 800}
        w.set_mode("expanded")
        assert ls.margins == {"right": 16, "bottom": 354}       # 874 − 520
        w.set_mode("compact")
        assert ls.margins == {"right": 16, "bottom": 800}
        assert w.margin == (16, 800)                              # a posição salva não muda
        # arrastar o expandido parte de onde ele aparece, não da posição salva (sem salto)
        w.set_mode("expanded")
        w._drag_begin(FakeGesture(), 0, 0, left=False)
        assert w._drag_start == (16, 354)
    finally:
        w.destroy()


# dois monitores lado a lado: notebook (1600×900 lógico, Waybar de 26 px) à direita de um ultrawide
LEFT = Output("HDMI-A-1", 0, 0, 2560, 1080, (0, 26, 0, 0))
RIGHT = Output("eDP-1", 2560, 0, 1600, 900, (0, 26, 0, 0))


def test_margin_and_screen_position_convert_both_ways():
    from hyprspot.ui.window import margin_to_pos, pos_to_margin
    for right in (True, False):
        for bottom in (True, False):
            pos = margin_to_pos((30, 40), (320, 64), RIGHT, right, bottom)
            assert pos_to_margin(pos, (320, 64), RIGHT, right, bottom) == (30, 40)
    assert margin_to_pos((16, 16), (320, 64), RIGHT, True, True) == (2560 + 1600 - 16 - 320, 26 + 874 - 16 - 64)


def test_drag_carries_the_widget_to_the_other_monitor(win):
    # o widget não fica preso ao monitor onde começou: soltando com o cursor no vizinho, ele vai
    # para lá (a layer passa a ser desse monitor e a margem é contada a partir dele). Enquanto o
    # botão está apertado ele fica no monitor de origem, encostado na borda: trocar de monitor
    # recria a superfície e o Hyprland perde o arrasto.
    w, _ls = win
    saved = []
    w._on_margin_changed = saved.append
    w.margin = (16, 16)
    w._horiz, w._vert = "right", "bottom"
    w.output = "eDP-1"
    w.fake_cursor["outs"] = [LEFT, RIGHT]
    g = FakeGesture()
    w.fake_cursor["pos"] = (4000.0, 850.0)           # sobre o widget, no notebook
    w._drag_begin(g, 0, 0, left=True)
    w.fake_cursor["pos"] = (3000.0, 850.0)
    w._drag_update(g, -50, 0, left=True)
    w._drag_tick()
    assert w.bound == [] and w.margin == (1016, 16)
    w.fake_cursor["pos"] = (2000.0, 600.0)           # cursor já no ultrawide
    w._drag_tick()
    assert w.bound == [] and w.margin[0] == 1600 - 320   # encostado na borda esquerda do notebook
    assert w.has_css_class("hs-crossing")
    w._drag_end(g, 0, 0, left=True)
    assert w.bound == ["HDMI-A-1"] and w.output == "HDMI-A-1"
    # agarrado 176 × 30 px dentro do widget: o canto vai para (1824, 570) no ultrawide
    assert w.margin == (2560 - 320 - 1824, 1054 - 64 - (570 - 26))
    assert saved == [w.margin] and not w.has_css_class("hs-crossing")


def test_unbound_widget_is_not_rebound_when_dropped_on_its_own_monitor(win):
    w, _ls = win
    w.margin = (16, 16)
    w._horiz, w._vert = "right", "bottom"
    w.fake_cursor["outs"] = [LEFT, RIGHT]             # w.output == "": o compositor escolheu
    g = FakeGesture()
    w.fake_cursor["pos"] = (2400.0, 1000.0)
    w._drag_begin(g, 0, 0, left=True)
    w.fake_cursor["pos"] = (2300.0, 900.0)
    w._drag_update(g, -100, -100, left=True)
    w._drag_end(g, 0, 0, left=True)
    assert w.bound == [] and w.margin == (116, 116)


def test_pushing_against_a_shared_edge_is_not_a_hide_request(win):
    # encostar na divisa entre os monitores não é "quero tirar da tela": do outro lado tem tela
    w, _ls = win
    hints = []
    w._on_push_offscreen = lambda: hints.append(1)
    w.margin = (16, 16)
    w._horiz, w._vert = "right", "bottom"
    w.output = "HDMI-A-1"
    w.fake_cursor["outs"] = [LEFT, RIGHT]
    g = FakeGesture()
    w.fake_cursor["pos"] = (2500.0, 1000.0)          # agarrado na ponta esquerda do widget
    w._drag_begin(g, 0, 0, left=True)
    w.fake_cursor["pos"] = (2555.0, 1000.0)          # o widget passa 200+ px da divisa
    w._drag_update(g, 55, 0, left=True)
    w._drag_end(g, 0, 0, left=True)
    assert hints == [] and w.margin[0] == 0 and w.bound == []


def test_saved_monitor_is_bound_at_startup(monkeypatch):
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.init()
    from hyprspot.config import Config
    from hyprspot.ui import window as W

    bound = []
    monkeypatch.setattr(W, "LS", FakeLS())
    monkeypatch.setattr(W, "EDGES", {"top": "top", "bottom": "bottom", "left": "left", "right": "right"})
    monkeypatch.setattr(W.HyprspotWindow, "_bind_output", lambda self, name: bound.append(name) or True)
    w = W.HyprspotWindow(None, Config(client_id="", monitor="HDMI-A-1"), Gtk.Box(), None, lambda m: None,
                         cursor_pos=lambda: None, outputs=lambda: [])
    try:
        assert bound == ["HDMI-A-1"]
    finally:
        w.destroy()
