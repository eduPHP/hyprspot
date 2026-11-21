"""Gera as imagens do README com dados de exemplo (músicas de rock conhecidas; playlists e capas inventadas).

    python3 scripts/make_screenshots.py [pasta]     # padrão: docs/screenshots

Precisa de uma sessão Wayland: as janelas aparecem por um instante enquanto são desenhadas.
Nada aqui fala com o Spotify nem lê a configuração do usuário.
"""
from __future__ import annotations

import colorsys
import math
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Como o app real, as janelas viram camadas (layer shell): o Hyprland não as encaixa no
# mosaico e cada uma fica exatamente do tamanho do widget. A lib precisa vir no LD_PRELOAD.
if "libgtk4-layer-shell" not in os.environ.get("LD_PRELOAD", ""):
    for d in ("/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu", "/usr/lib64", "/usr/lib"):
        lib = Path(d) / "libgtk4-layer-shell.so.0"
        if lib.exists():
            os.environ["LD_PRELOAD"] = f"{lib}:{os.environ.get('LD_PRELOAD', '')}".rstrip(":")
            os.execv(sys.executable, [sys.executable, *sys.argv])

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LS  # noqa: E402

from hyprspot.model import Playlist, Track  # noqa: E402
from hyprspot.state import AppState, Playback  # noqa: E402
from hyprspot.ui.common import load_css  # noqa: E402
from hyprspot.ui.compact import CompactView  # noqa: E402
from hyprspot.ui.expanded import ExpandedView  # noqa: E402

SCALE = 2          # imagens em 2x para ficarem nítidas
PAD = 28           # moldura ao redor do widget
BG = ((0.11, 0.12, 0.20), (0.27, 0.16, 0.34))  # degradê do "papel de parede"

NOW = Track("spotify:track:fake01", "Highway Star", "Deep Purple", "Machine Head", 365_000, "art:1")
PLAYLISTS = [
    Playlist("spotify:user:fake:collection", "Músicas Curtidas", "Sua biblioteca", None),
    Playlist("spotify:playlist:p1", "Rock Clássico", "rockzone", "art:2"),
    Playlist("spotify:playlist:p2", "Hard Rock dos Anos 80", "pedro.r", "art:3"),
    Playlist("spotify:playlist:p3", "Grunge Anos 90", "seattle.fm", "art:4"),
    Playlist("spotify:playlist:p4", "Rock Nacional", "radar", "art:5"),
    Playlist("spotify:playlist:p5", "Riffs para codar", "devbeats", "art:6"),
    Playlist("spotify:playlist:p6", "Estrada e Guitarra", "joao.p", "art:7"),
]
# fila "A seguir" da tela da playlist (Rock Clássico tocando)
QUEUE = [
    Track("spotify:track:q1", "Smoke on the Water", "Deep Purple", "", 340_000, "art:13"),
    Track("spotify:track:q2", "Back in Black", "AC/DC", "", 255_000, "art:14"),
    Track("spotify:track:q3", "Sweet Child O' Mine", "Guns N' Roses", "", 356_000, "art:15"),
    Track("spotify:track:q4", "Hotel California", "Eagles", "", 391_000, "art:16"),
    Track("spotify:track:q5", "Paranoid", "Black Sabbath", "", 168_000, "art:17"),
    Track("spotify:track:q6", "Born to Be Wild", "Steppenwolf", "", 211_000, "art:18"),
    Track("spotify:track:q7", "Stairway to Heaven", "Led Zeppelin", "", 482_000, "art:19"),
]
SEARCH_PLAYLISTS = [
    Playlist("spotify:playlist:s1", "Highway Rock", "rockzone", "art:20"),
    Playlist("spotify:playlist:s2", "Road Trip Classics", "joao.p", "art:21"),
]
QUERY = "highway"
RESULTS = [
    NOW,
    Track("spotify:track:fake02", "Highway to Hell", "AC/DC", "", 208_000, "art:8"),
    Track("spotify:track:fake03", "Life Is a Highway", "Tom Cochrane", "", 266_000, "art:9"),
    Track("spotify:track:fake04", "Highway Tune", "Greta Van Fleet", "", 276_000, "art:10"),
    Track("spotify:track:fake05", "Lost Highway", "Bon Jovi", "", 233_000, "art:11"),
    Track("spotify:track:fake06", "Highway Chile", "The Jimi Hendrix Experience", "", 212_000, "art:12"),
]


def fake_cover(seed: int, px: int = 256) -> Gdk.Texture:
    """Capa abstrata: degradê e círculos/faixas em cores derivadas da semente."""
    rnd = random.Random(seed * 7919)
    h = rnd.random()
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, px, px)
    cr = cairo.Context(surf)
    g = cairo.LinearGradient(0, 0, px, px)
    for stop, (dh, s, v) in ((0, (0, 0.55, 0.85)), (1, (0.12, 0.65, 0.35))):
        g.add_color_stop_rgb(stop, *colorsys.hsv_to_rgb((h + dh) % 1, s, v))
    cr.set_source(g)
    cr.paint()
    for _ in range(rnd.randint(2, 4)):
        r, gg, b = colorsys.hsv_to_rgb((h + rnd.uniform(0.3, 0.7)) % 1, rnd.uniform(0.3, 0.7), rnd.uniform(0.7, 1))
        cr.set_source_rgba(r, gg, b, rnd.uniform(0.35, 0.75))
        if rnd.random() < 0.6:
            cr.arc(rnd.uniform(0.2, 0.8) * px, rnd.uniform(0.2, 0.8) * px, rnd.uniform(0.12, 0.35) * px, 0, 2 * math.pi)
        else:
            y = rnd.uniform(0.2, 0.8) * px
            cr.rectangle(0, y, px, rnd.uniform(0.05, 0.15) * px)
        cr.fill()
    data = GLib.Bytes.new(bytes(surf.get_data()))
    return Gdk.MemoryTexture.new(px, px, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED, data, surf.get_stride())


class FakeArt:
    def __init__(self):
        self.cache: dict[str, Gdk.Texture] = {}

    def load(self, url, cb):
        if url not in self.cache:
            self.cache[url] = fake_cover(int(url.split(":")[1]))
        cb(self.cache[url])


def render(widget: Gtk.Widget, w: int, h: int, radius: float, out: Path, frame: bool = True) -> None:
    """Desenha o widget sobre o degradê (ou sozinho) e grava um PNG em 2x."""
    pad = PAD if frame else 0
    W, H = w + 2 * pad, h + 2 * pad
    snap = Gtk.Snapshot()
    snap.scale(SCALE, SCALE)
    if frame:
        rect = Graphene.Rect().init(0, 0, W, H)
        stops = []
        for off, rgb in ((0.0, BG[0]), (1.0, BG[1])):
            st = Gsk.ColorStop()
            st.offset = off
            st.color = Gdk.RGBA(red=rgb[0], green=rgb[1], blue=rgb[2], alpha=1)
            stops.append(st)
        snap.append_linear_gradient(rect, Graphene.Point().init(0, 0), Graphene.Point().init(W, H), stops)
        rr = Gsk.RoundedRect().init_from_rect(Graphene.Rect().init(pad, pad, w, h), radius)
        snap.append_outset_shadow(rr, Gdk.RGBA(red=0, green=0, blue=0, alpha=0.45), 0, 10, 0, 24)
        snap.translate(Graphene.Point().init(pad, pad))
    Gtk.WidgetPaintable(widget=widget).snapshot(snap, w, h)
    node = snap.to_node()
    tex = widget.get_native().get_renderer().render_texture(node, Graphene.Rect().init(0, 0, W * SCALE, H * SCALE))
    tex.save_to_png(str(out))
    print(f"  {out}")


def as_layer(win: Gtk.Window) -> Gtk.Window:
    LS.init_for_window(win)
    LS.set_namespace(win, "hyprspot-screenshot")
    LS.set_layer(win, LS.Layer.OVERLAY)
    LS.set_anchor(win, LS.Edge.TOP, True)
    LS.set_anchor(win, LS.Edge.LEFT, True)
    LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)
    return win


def host(child: Gtk.Widget, title: str) -> Gtk.Window:
    win = as_layer(Gtk.Window(title=title, decorated=False))
    win.add_css_class("hs-window")
    win.set_child(child)
    win.present()
    return win


def widget_shots(out: Path, done) -> None:
    load_css()
    art = FakeArt()
    state = AppState()
    state.set_playback(Playback(connected=True, logged_in=True, is_active=True, status="paused", track=NOW,
                                context_uri=PLAYLISTS[1].uri, position_ms=82_000, volume=56))
    state.set_queue(PLAYLISTS[1].uri, QUEUE)
    state.set_liked(True)
    state.set_playlists(PLAYLISTS)
    noop = lambda *_a, **_k: None  # noqa: E731

    compact = CompactView(state, noop, noop, art)
    expanded = ExpandedView(state, noop, noop, noop, noop, art)
    wins = [host(compact, "hyprspot-shot-compact"), host(expanded, "hyprspot-shot-expanded")]
    state.set_ui(mode="expanded", tab="playlists")

    def step1():
        render(compact, CompactView.WIDTH, CompactView.HEIGHT, 14, out / "compact.png")
        render(expanded, ExpandedView.WIDTH, ExpandedView.HEIGHT, 16, out / "expanded-playlists.png")
        state.set_ui(tab="search")
        expanded.entry.set_text(QUERY)
        state.set_search(QUERY, RESULTS, SEARCH_PLAYLISTS)
        GLib.timeout_add(700, step2)
        return False

    def step2():
        render(expanded, ExpandedView.WIDTH, ExpandedView.HEIGHT, 16, out / "expanded-search.png")
        state.set_ui(tab="playlists")
        expanded.open_playlist(PLAYLISTS[1], "playlists")
        GLib.timeout_add(700, step3)
        return False

    def step3():
        render(expanded, ExpandedView.WIDTH, ExpandedView.HEIGHT, 16, out / "expanded-playlist-page.png")
        for w in wins:
            w.destroy()
        done()
        return False

    GLib.timeout_add(2500, step1)  # com menos tempo o compositor ainda não desenhou e a imagem sai vazia


def setup_shots(out: Path, done) -> None:
    sys.path.insert(0, str(ROOT / "tests"))
    from test_setup_window import Fake  # ações falsas dos testes

    from hyprspot.ui import setup_window as sw

    from hyprspot.ui import theme
    theme.load(sw.CSS_PATH)
    cid = "3f9a2c7e51d84b06a8e2f41c97d05b6e"
    fake = Fake()
    win = as_layer(sw.SetupWindow(None, fake))
    win.set_size_request(580, 600)
    win.present()
    plan = [
        ("setup-2-key", 1, lambda: win.key_entry.set_text("x" * 44)),
        ("setup-3-app", 2, lambda: win.cid_entry.set_text(cid)),
        ("setup-4-login", 3, None),
        ("setup-5-pair", 4, None),
        ("setup-6-done", 5, None),
    ]

    def go():
        if not plan:
            win._stop_poll()
            win.destroy()
            done()
            return False
        name, idx, prep = plan.pop(0)
        if idx == 3:
            fake.cid = fake.token_for = cid
        win._show(idx)
        if prep:
            prep()
        GLib.timeout_add(800, lambda: (render(win, win.get_width(), win.get_height(), 0,
                                               out / f"{name}.png", frame=False), go(), False)[2])
        return False

    GLib.timeout_add(700, go)


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    Gtk.init()
    loop = GLib.MainLoop()
    print(f"Gerando imagens em {out}")
    widget_shots(out, lambda: setup_shots(out, loop.quit))
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
