"""hyprspot's colours, following the desktop theme when there is one.

The stylesheets use @hs_* names. Their Tokyo Night values below are the default; when hypr-ui's
theme file exists (~/.local/state/hypr-ui/theme.json, written from the Omarchy theme by
hypr-ui/theme.py) the names are mapped from it instead, and a theme switch restyles hyprspot live.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

THEME_FILE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "hypr-ui/theme.json"

DEFAULTS = {
    "hs_fg": "#c0caf5",        # titles, primary text
    "hs_text": "#a9b1d6",      # body text, idle buttons
    "hs_sub": "#9aa5ce",       # artists, meta, tabs
    "hs_muted": "#8089b3",     # placeholders, hints
    "hs_faint": "#565f89",     # disabled, paused progress
    "hs_accent": "#7aa2f7",
    "hs_accent_hover": "#8db0f8",
    "hs_line": "#292e42",      # troughs, borders, selected tab
    "hs_line_strong": "#3b4261",
    "hs_raised": "#24283b",    # hover rows, cover placeholder
    "hs_surface": "#1f2335",
    "hs_base": "#1a1b26",      # the card
    "hs_bg": "#16161e",        # wells: tabs, search
    "hs_bright": "#d5dcfa",    # hover on light controls
    "hs_red": "#f7768e",
    "hs_amber": "#e0af68",
    "hs_green": "#9ece6a",
}


def _mix(a: str, b: str, t: float) -> str:
    a, b = a.lstrip("#"), b.lstrip("#")
    return "#" + "".join(f"{round(int(a[i:i + 2], 16) * (1 - t) + int(b[i:i + 2], 16) * t):02x}" for i in (0, 2, 4))


def colors() -> dict[str, str]:
    try:
        data = json.loads(THEME_FILE.read_text())
        c, p = data["color"], data.get("palette", {})
    except (OSError, ValueError, KeyError, TypeError):
        return dict(DEFAULTS)
    light = data.get("mode") == "light"
    base = p.get("background", c["bg"])[:7]
    return {
        "hs_fg": c["fg"],
        "hs_text": c["fg"] if light else p.get("foreground", c["fg"])[:7],
        "hs_sub": _mix(c["fg"], c["muted"], 0.45),
        "hs_muted": c["muted"],
        "hs_faint": c["faint"],
        "hs_accent": c["blue"],
        "hs_accent_hover": _mix(c["blue"], c["bright"], 0.15),
        "hs_line": c["line"],
        "hs_line_strong": c["track"],
        "hs_raised": c["raised"],
        "hs_surface": c["surface"],
        "hs_base": base,
        "hs_bg": c["bg"],
        "hs_bright": c["bright"],
        "hs_red": c["red"],
        "hs_amber": c["amber"],
        "hs_green": c["green"],
    }


def hex(name: str) -> str:
    """A colour for Pango markup, e.g. hex("hs_fg")."""
    return colors()[name]


_providers: list[tuple[Gtk.CssProvider, Path]] = []
_watch: dict = {"monitor": None, "pending": 0}


def _text(path: Path) -> str:
    return "".join(f"@define-color {k} {v};\n" for k, v in colors().items()) + path.read_text()


def _reload() -> bool:
    _watch["pending"] = 0
    for provider, path in _providers:
        provider.load_from_string(_text(path))
    return False


def load(path: Path, priority: int = Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1) -> Gtk.CssProvider:
    """Load a stylesheet with the theme colours in front of it, and keep it following the theme."""
    provider = Gtk.CssProvider()
    provider.load_from_string(_text(path))
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, priority)
    _providers.append((provider, path))
    if _watch["monitor"] is None:
        THEME_FILE.parent.mkdir(parents=True, exist_ok=True)
        mon = Gio.File.new_for_path(str(THEME_FILE)).monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)

        def changed(*_):
            if not _watch["pending"]:
                _watch["pending"] = GLib.timeout_add(150, _reload)

        mon.connect("changed", changed)
        _watch["monitor"] = mon
    return provider
