"""Janela layer-shell (OVERLAY) que alterna entre compacto e expandido."""
from __future__ import annotations

import os
import sys
import time
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LS  # noqa: E402

from hyprspot.config import Config  # noqa: E402
from hyprspot.focus import Output, hypr_cursor, hypr_outputs, output_at  # noqa: E402

_DEBUG = bool(os.environ.get("HYPRSPOT_DEBUG"))


def _log(*args) -> None:
    if _DEBUG:
        print(f"[drag {time.monotonic():.3f}]", *args, file=sys.stderr, flush=True)


EDGES = {"top": LS.Edge.TOP, "bottom": LS.Edge.BOTTOM, "left": LS.Edge.LEFT, "right": LS.Edge.RIGHT}
DRAG_THRESHOLD = 8  # px; abaixo disso o botão esquerdo continua sendo um clique
FOLLOW_TICK_MS = 16
FOLLOW_IDLE_S = 0.4  # seguindo: o Hyprland não entrega o soltar do botão; cursor parado este tempo = soltou
PUSH_OFFSCREEN_PX = 48  # empurrar além da borda mais que isto conta como "quero tirar da tela"


def drag_margin(start: tuple[int, int], dx: float, dy: float, right: bool, bottom: bool) -> tuple[int, int]:
    """Margem nova depois de arrastar (dx, dy); a margem conta a partir da borda ancorada."""
    sx = -1 if right else 1
    sy = -1 if bottom else 1
    return max(0, int(start[0] + sx * dx)), max(0, int(start[1] + sy * dy))


def clamp_margin(margin: tuple[int, int], size: tuple[int, int], area: tuple[int, int]) -> tuple[int, int]:
    """Mantém o widget inteiro dentro da área útil do monitor."""
    return (max(0, min(margin[0], area[0] - size[0])), max(0, min(margin[1], area[1] - size[1])))


def margin_to_pos(margin: tuple[float, float], size: tuple[int, int], out: Output,
                  right: bool, bottom: bool) -> tuple[float, float]:
    """Canto superior esquerdo do widget na tela (global) para a margem dada no monitor."""
    (ox, oy), (aw, ah) = out.origin, out.area
    x = ox + (aw - size[0] - margin[0] if right else margin[0])
    y = oy + (ah - size[1] - margin[1] if bottom else margin[1])
    return x, y


def pos_to_margin(pos: tuple[float, float], size: tuple[int, int], out: Output,
                  right: bool, bottom: bool) -> tuple[float, float]:
    """Inverso de margin_to_pos (pode sair negativa ou maior que a tela: quem chama limita)."""
    (ox, oy), (aw, ah) = out.origin, out.area
    x, y = pos[0] - ox, pos[1] - oy
    return (aw - size[0] - x if right else x), (ah - size[1] - y if bottom else y)


def offscreen_push(raw: tuple[float, float], m: tuple[int, int], size: tuple[int, int], out: Output,
                   outs: list[Output], right: bool, bottom: bool) -> float:
    """Quanto o arrasto passou da borda, contando só bordas sem outro monitor do outro lado."""
    x, y = margin_to_pos(m, size, out, right, bottom)
    cx, cy = x + size[0] / 2, y + size[1] / 2
    push = 0.0
    # margem maior que a permitida numa âncora à direita = passou da borda esquerda (e vice-versa)
    for over, probe in ((raw[0] - m[0], (x - 1 if (raw[0] > m[0]) == right else x + size[0], cy)),
                        (raw[1] - m[1], (cx, y - 1 if (raw[1] > m[1]) == bottom else y + size[1]))):
        if over and not any(o.contains(*probe) for o in outs):
            push = max(push, abs(over))
    return push


class HyprspotWindow(Gtk.ApplicationWindow):
    def __init__(self, app, config: Config, compact: Gtk.Widget, expanded: Gtk.Widget | None,
                 on_margin_changed: Callable[[tuple[int, int]], None],
                 on_compact_key: Callable[[int], bool] | None = None,
                 cursor_pos: Callable[[], tuple[float, float] | None] = hypr_cursor,
                 outputs: Callable[[], list[Output]] = hypr_outputs,
                 on_push_offscreen: Callable[[], None] | None = None):
        super().__init__(application=app, title="hyprspot")
        self.config = config
        self.compact = compact
        self.expanded = expanded
        self.mode = "compact"
        self._on_margin_changed = on_margin_changed
        self.add_css_class("hs-window")
        self.set_decorated(False)

        LS.init_for_window(self)
        LS.set_namespace(self, "hyprspot")
        LS.set_layer(self, LS.Layer.OVERLAY)
        LS.set_exclusive_zone(self, 0)
        self._outputs = outputs
        self.output = ""  # monitor ao qual a layer está presa ("" = o compositor escolhe)
        if config.monitor:
            self._bind_output(config.monitor)
        vert, horiz = config.anchor.split("-")
        self._vert, self._horiz = EDGES[vert], EDGES[horiz]
        LS.set_anchor(self, self._vert, True)
        LS.set_anchor(self, self._horiz, True)
        self.margin = tuple(config.margin)
        self._apply_margin()
        LS.set_keyboard_mode(self, LS.KeyboardMode.NONE)

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.box.append(compact)
        if expanded is not None:
            self.box.append(expanded)
            expanded.set_visible(False)
        self.set_child(self.box)

        # Mover: arrastar com o botão do meio (sempre) ou com o esquerdo (só no compacto, para
        # o touchpad). A posição vem do cursor na tela (socket do Hyprland), lida a cada quadro:
        # o deslocamento do GTK é relativo à janela, que anda junto, e faria o widget tremer.
        # Sem o socket, o widget só muda de lugar ao soltar.
        self._cursor_pos = cursor_pos
        self._drag_outs: list[Output] = []
        self._drag_out: Output | None = None
        self._grab_offset = (0.0, 0.0)
        self._drag_home: str | None = None  # monitor onde o arrasto começou
        self._follow_id = 0  # seguindo o cursor depois que o Hyprland encerrou o arrasto (ver _drag_end)
        self._follow_last: tuple[tuple[float, float] | None, float] = (None, 0.0)
        self._drag_overshoot = 0.0
        self._on_push_offscreen = on_push_offscreen
        self._drag_start = self.margin
        self._drag_cursor0: tuple[float, float] | None = None
        self._dragging = False
        self._tick_id = 0
        for button, left in ((2, False), (1, True)):
            drag = Gtk.GestureDrag(button=button)
            if left:  # antes dos botões, para cancelar o clique quando vira arrasto
                drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            drag.connect("drag-begin", lambda g, x, y, lf=left: self._drag_begin(g, x, y, left=lf))
            drag.connect("drag-update", lambda g, x, y, lf=left: self._drag_update(g, x, y, left=lf))
            drag.connect("drag-end", lambda g, x, y, lf=left: self._drag_end(g, x, y, left=lf))
            self.add_controller(drag)

        # Com o mouse parado sobre o compacto, o widget pega o teclado (para "q" fechar);
        # ao sair, devolve. 250 ms de espera evitam roubar o foco quando o mouse só passa por cima.
        self._on_compact_key = on_compact_key
        self._hover_id = 0
        self._grabbed = False
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self._hover_enter())
        motion.connect("leave", lambda *_: self._hover_leave())
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)
        # Seguindo depois da troca de monitor (_drag_end), o soltar do botão chega à superfície
        # nova sem o apertar correspondente: o GestureClick avisa com "unpaired-release".
        release = Gtk.GestureClick(button=0)
        release.connect("unpaired-release", self._on_unpaired_release)
        self.add_controller(release)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    # -- modos --
    def set_mode(self, mode: str) -> None:
        if mode == self.mode or (mode == "expanded" and self.expanded is None):
            return
        self.mode = mode
        exp = mode == "expanded"
        self.compact.set_visible(not exp)
        if self.expanded is not None:
            self.expanded.set_visible(exp)
        w = self.expanded if exp else self.compact
        self.set_default_size(*w.get_size_request())
        # o expandido é bem maior: perto da borda oposta à âncora ele sairia da tela. Mostra-o
        # numa posição ajustada; a posição escolhida (self.margin) volta ao recolher.
        self._apply_margin(self._fit_margin(w.get_size_request()) if exp else None)
        self._grabbed = False
        LS.set_keyboard_mode(self, LS.KeyboardMode.EXCLUSIVE if exp else LS.KeyboardMode.NONE)
        if exp:
            self.present()

    HOVER_DWELL_MS = 250

    def _hover_enter(self) -> None:
        if self.mode != "compact" or self._hover_id or self._grabbed:
            return

        def grab():
            self._hover_id = 0
            self._grab_keyboard()
            return False

        self._hover_id = GLib.timeout_add(self.HOVER_DWELL_MS, grab)

    def _grab_keyboard(self) -> None:
        if self.mode == "compact":
            LS.set_keyboard_mode(self, LS.KeyboardMode.EXCLUSIVE)
            self._grabbed = True

    def _on_motion(self, _ctrl, x: float, y: float) -> None:
        if self._follow_id:
            return  # o estado dos botões no movimento não vale aqui: o GTK o zera ao trocar de superfície
        # Com o teclado exclusivo, o Hyprland continua mandando o movimento para o widget
        # mesmo com o cursor fora dele e não manda "leave". Sem isto, o widget prendia o
        # teclado e o mouse (e um "q" digitado em outra janela o fechava).
        if self.mode != "compact":
            return
        if 0 <= x < self.get_width() and 0 <= y < self.get_height():
            self._hover_enter()  # o "enter" pode chegar com o cursor ainda na borda, fora
        elif self._hover_id or self._grabbed:
            self._hover_leave()

    def _hover_leave(self) -> None:
        if self._hover_id:
            GLib.source_remove(self._hover_id)
            self._hover_id = 0
        if self.mode == "compact":
            LS.set_keyboard_mode(self, LS.KeyboardMode.NONE)
            self._grabbed = False

    def _on_key(self, _c, keyval, _code, _state) -> bool:
        if self.mode == "compact" and self._on_compact_key is not None:
            return self._on_compact_key(keyval)
        return False

    def toggle_hidden(self) -> None:
        self.set_visible(not self.get_visible())

    # -- arrastar para mover --
    def _apply_margin(self, margin: tuple[int, int] | None = None) -> None:
        m = self._shown_margin = margin or self.margin
        LS.set_margin(self, self._horiz, m[0])
        LS.set_margin(self, self._vert, m[1])

    def _size(self) -> tuple[int, int]:
        return self.get_width(), self.get_height()

    def _anchors(self) -> tuple[bool, bool]:
        return self._horiz == LS.Edge.RIGHT, self._vert == LS.Edge.BOTTOM

    def _current_output(self, outs: list[Output], pos: tuple[float, float] | None) -> Output | None:
        """O monitor onde o widget está: o que ele prendeu ou, sem isso, o do cursor."""
        mine = next((o for o in outs if o.name == self.output), None)
        return mine or (output_at(outs, *pos) if pos else output_at(outs, -1e9, -1e9))

    def _bind_output(self, name: str) -> bool:
        """Prende a layer ao monitor de conector `name` (troca de tela mesmo já mapeada)."""
        display = Gdk.Display.get_default()
        mons = display.get_monitors() if display else None
        for i in range(mons.get_n_items() if mons else 0):
            mon = mons.get_item(i)
            if mon.get_connector() == name:
                LS.set_monitor(self, mon)
                self.output = name
                _log("bound", name)
                return True
        _log("bind failed", name, [mons.get_item(i).get_connector() for i in range(mons.get_n_items() if mons else 0)])
        return False

    def _fit_margin(self, size: tuple[int, int]) -> tuple[int, int]:
        outs = self._outputs()
        out = self._current_output(outs, self._cursor_pos())
        return clamp_margin(self.margin, size, out.area) if out else self.margin

    def _drag_begin(self, _g, _x: float, _y: float, left: bool) -> None:
        if self._follow_id:
            self._finish_follow("new drag")
        self._drag_start = self._shown_margin  # no expandido pode estar ajustada para caber
        self._dragging = False
        self._drag_cursor0 = self._cursor_pos()
        self._drag_outs = self._outputs() if self._drag_cursor0 else []
        # o cursor está sobre o widget: o monitor dele é o do widget
        self._drag_out = output_at(self._drag_outs, *self._drag_cursor0) if self._drag_outs else None
        self._drag_home = self._drag_out.name if self._drag_out else None
        if self._drag_out:
            x, y = margin_to_pos(self._drag_start, self._size(), self._drag_out, *self._anchors())
            self._grab_offset = (self._drag_cursor0[0] - x, self._drag_cursor0[1] - y)
        self._drag_overshoot = 0.0
        _log("begin", "left" if left else "middle", "cursor", self._drag_cursor0, "home", self._drag_home,
             "output", repr(self.output), "outs", [(o.name, o.x, o.y, o.w, o.h) for o in self._drag_outs])

    def _drag_update(self, g, dx: float, dy: float, left: bool) -> None:
        if self._dragging or (left and self.mode != "compact"):
            return
        if left and (dx * dx + dy * dy) < DRAG_THRESHOLD ** 2:
            return
        self._dragging = True
        _log("dragging", "left" if left else "middle", (dx, dy))
        g.set_state(Gtk.EventSequenceState.CLAIMED)  # o botão sob o cursor não recebe o clique
        self.add_css_class("hs-dragging")
        self.set_cursor_from_name("grabbing")
        if self._drag_cursor0 is not None:
            self._tick_id = self.add_tick_callback(lambda *_: (self._drag_tick(), True)[1])
            self._drag_tick()

    def _margin_at_cursor(self, final: bool = False) -> tuple[tuple[int, int], Output | None] | None:
        """Margem para a posição atual do cursor, e o monitor a que ela se refere.

        Trocar a layer de monitor recria a superfície e o arrasto se perde (o Hyprland tira o
        botão apertado da superfície nova). Por isso, enquanto o botão está apertado, o widget
        fica no monitor onde o arrasto começou, encostado na borda mais próxima do cursor;
        só ao soltar (final=True) ele vai para o monitor sob o cursor.
        """
        c0 = self._drag_cursor0
        c = self._cursor_pos() if c0 is not None else None
        if c is None:
            return None
        right, bottom = self._anchors()
        size = self._size()
        if not self._drag_outs:  # sem a lista de monitores: segue o cursor, sem limite
            sx, sy = -1 if right else 1, -1 if bottom else 1
            raw = (self._drag_start[0] + sx * (c[0] - c0[0]), self._drag_start[1] + sy * (c[1] - c0[1]))
            return (max(0, int(raw[0])), max(0, int(raw[1]))), None
        target = output_at(self._drag_outs, *c)
        home = next((o for o in self._drag_outs if o.name == self._drag_home), target)
        out = target if final else home
        # o ponto que foi agarrado continua sob o cursor
        pos = (c[0] - self._grab_offset[0], c[1] - self._grab_offset[1])
        raw = pos_to_margin(pos, size, out, right, bottom)
        m = clamp_margin((int(raw[0]), int(raw[1])), size, out.area)  # fora da tela: ponto visível mais próximo
        self._drag_overshoot = offscreen_push(raw, m, size, out, self._drag_outs, right, bottom)
        # aviso visual: soltar agora leva o widget para o outro monitor
        if target is not home and not final:
            self.add_css_class("hs-crossing")
        else:
            self.remove_css_class("hs-crossing")
        return m, out

    def _drag_tick(self) -> None:
        got = self._margin_at_cursor() if self._dragging else None
        if got is not None and got[0] != self.margin:
            _log("tick", self._cursor_pos(), "->", got[0], got[1].name if got[1] else None)
        if got is not None and got[0] != self.margin:
            self.margin = got[0]
            self._apply_margin()

    def _drag_end(self, _g, dx: float, dy: float, left: bool) -> None:
        _log("drag-end signal", "left" if left else "middle", "dragging", self._dragging, (dx, dy))
        if not self._dragging:
            return
        self._dragging = False
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        got = self._margin_at_cursor(final=True)
        _log("end", "gtk", (dx, dy), "cursor", self._cursor_pos(), "got", got and (got[0], got[1] and got[1].name),
             "output", repr(self.output), "home", self._drag_home)
        if got is None:
            self.margin = drag_margin(self._drag_start, dx, dy, *self._anchors())
            self._apply_margin()
            self._drop()
            return
        self._place(*got)
        if got[1] is not None and got[1].name != self._drag_home:
            # O Hyprland encerra o arrasto de uma layer assim que o cursor passa para outro
            # monitor (visto no log: o "fim" chega sempre na divisa, com o botão ainda apertado).
            # O widget muda de monitor ali e continua seguindo o cursor até o botão ser solto:
            # o soltar sem par (_on_unpaired_release) ou, se ele não chegar, o cursor parado
            # por FOLLOW_IDLE_S. (O estado dos botões nos eventos de movimento vem zerado.)
            self._follow_last = (self._cursor_pos(), time.monotonic())
            self._follow_id = GLib.timeout_add(FOLLOW_TICK_MS, self._follow_tick)
            _log("follow start")
            return
        self._drop()

    def _place(self, m: tuple[int, int], out: Output | None) -> None:
        self.margin = m
        self._apply_margin()
        if out is not None and out.name != self.output and (self.output or out.name != self._drag_home):
            self._bind_output(out.name)  # outro monitor: a layer é recriada lá

    def _on_unpaired_release(self, _g, _x: float, _y: float, button: int, _seq) -> None:
        _log("unpaired release", button, "following", bool(self._follow_id))
        if self._follow_id:
            self._finish_follow("button released")

    def _follow_tick(self) -> bool:
        c = self._cursor_pos()
        got = self._margin_at_cursor(final=True)
        if got is not None and (got[0] != self.margin or (got[1] and got[1].name != self.output)):
            self._place(*got)
        last, since = self._follow_last
        now = time.monotonic()
        if c != last:
            self._follow_last = (c, now)
        elif now - since > FOLLOW_IDLE_S:
            self._follow_id = 0
            self._finish_follow("cursor idle")
            return False
        return True

    def _finish_follow(self, why: str) -> None:
        _log("follow end", why, "cursor", self._cursor_pos(), "margin", self.margin, "output", self.output)
        if self._follow_id:
            GLib.source_remove(self._follow_id)
            self._follow_id = 0
        self._drop()

    def _drop(self) -> None:
        self.remove_css_class("hs-dragging")
        self.remove_css_class("hs-crossing")
        self.set_cursor(None)
        self._on_margin_changed(self.margin)
        if self._drag_overshoot > PUSH_OFFSCREEN_PX and self._on_push_offscreen:
            self._on_push_offscreen()
