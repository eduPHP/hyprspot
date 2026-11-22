"""Modo Waybar (puro): o texto do item custom/hyprspot e a instalação do item na config da Waybar.

O app grava o estado em $XDG_RUNTIME_DIR/hyprspot/waybar.json; `hyprspot waybar-status` (o `exec`
do item) acompanha esse arquivo e imprime uma linha JSON a cada mudança. Flutuando, ou com o app
fechado, o texto sai vazio e a Waybar esconde o item.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path


from hyprspot.presenter import now_playing
from hyprspot.state import Playback

RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "hyprspot"
STATUS_PATH = RUNTIME / "waybar.json"
WAYBAR_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "waybar"
MODULE = "custom/hyprspot"
MAX_CHARS = 40
ICON_PLAY, ICON_PAUSE, ICON_NOTE = "\U000f040a", "\U000f03e4", "\U000f075a"  # Nerd Font (md-play, md-pause, md-music)
HIDDEN = {"text": "", "tooltip": "", "class": "hidden"}


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cut(s: str, n: int = MAX_CHARS) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def status(pb: Playback, open_: bool, context_name: str | None = None, soloist_days: int | None = None) -> dict:
    """O JSON do item da barra para o estado atual."""
    v = now_playing(pb, context_name, soloist_days)
    tip = "\n".join(x for x in (v.title, v.detail or v.subtitle) if x)
    if v.offline:
        return {"text": ICON_NOTE, "tooltip": _esc(tip), "class": "disconnected"}
    if pb.track is None:
        text, cls = f"{ICON_NOTE}  {v.title}", "idle"
    else:
        line = " · ".join(x for x in (v.title, v.subtitle) if x)
        text = f"{ICON_PAUSE if v.playing else ICON_PLAY}  {_esc(_cut(line))}"
        cls = "playing" if v.playing else "paused"
    return {"text": text, "tooltip": _esc(tip), "class": ["open", cls] if open_ else cls}


def write_status(data: dict | None, path: Path = STATUS_PATH) -> bool:
    """Grava (atomicamente) se mudou. None apaga: o item some."""
    if data is None:
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
    text = json.dumps(data, ensure_ascii=False)
    try:
        if path.read_text() == text:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)
    return True


def stream(path: Path = STATUS_PATH, out=sys.stdout, poll_s: float = 0.25, once: bool = False) -> None:
    """O `exec` do item: uma linha por mudança do arquivo de estado."""
    last = None
    while True:
        try:
            line = path.read_text().strip() or json.dumps(HIDDEN)
        except OSError:
            line = json.dumps(HIDDEN)
        if line != last:
            out.write(line + "\n")
            out.flush()
            last = line
        if once:
            return
        time.sleep(poll_s)


# ---------------------------------------------------------------------- config da Waybar
def module_block(bin_dir: Path) -> str:
    ctl = bin_dir / "hyprspot-ctl"
    return f'''  // hyprspot: some da barra quando o hyprspot está flutuando (Ajustes > Posição)
  "{MODULE}": {{
    "exec": "{ctl} waybar",
    "return-type": "json",
    "restart-interval": 5,
    "tooltip": true,
    "on-click": "{ctl} bar-toggle",
    "on-click-right": "{ctl} media play-pause",
    "on-click-middle": "{ctl} media next",
    "on-scroll-up": "{ctl} volume-up",
    "on-scroll-down": "{ctl} volume-down"
  }},
'''


STYLE_BLOCK = """
/* custom/hyprspot (hyprspot na Waybar) */
#custom-hyprspot { padding: 0 7px; margin: 3px 4px; border-radius: 5px; }
#custom-hyprspot.paused, #custom-hyprspot.idle { color: @muted; }
#custom-hyprspot.open { background-color: alpha(@accent, 0.18); color: @accent; }
#custom-hyprspot.disconnected { background-color: alpha(@red, 0.15); color: @red; }
"""


def add_module(config_text: str, bin_dir: Path) -> str | None:
    """Põe o item no início de modules-right e a definição antes do fim. None: já está, ou não deu."""
    if f'"{MODULE}"' in config_text:
        return None
    key = '"modules-right"'
    i = config_text.find(key)
    if i < 0:
        return None
    j = config_text.find("[", i)
    end = config_text.rfind("}")
    if j < 0 or end < 0:
        return None
    first = config_text[j + 1:].lstrip()
    sep = ", " if not first.startswith("]") else ""
    text = config_text[: j + 1] + f'"{MODULE}"{sep}' + config_text[j + 1:].lstrip(" ")
    end = text.rfind("}")
    before = text[:end].rstrip()
    if not before.endswith((",", "{")):
        before += ","
    return before + "\n\n" + module_block(bin_dir).rstrip().rstrip(",") + "\n" + text[end:]


def install(bin_dir: Path, waybar_dir: Path = WAYBAR_DIR) -> str:
    """Adiciona o item à config e o estilo à folha da Waybar, com backup. Devolve o que fez."""
    cfg = next((p for p in (waybar_dir / "config.jsonc", waybar_dir / "config") if p.exists()), None)
    if cfg is None:
        return "sem config da Waybar"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    done = []
    new = add_module(cfg.read_text(), bin_dir)
    if new is not None:
        shutil.copy2(cfg, cfg.with_name(f"{cfg.name}.bak.hyprspot.{stamp}"))
        cfg.write_text(new)
        done.append("config")
    css = waybar_dir / "style.css"
    if css.exists() and "#custom-hyprspot" not in css.read_text():
        shutil.copy2(css, css.with_name(f"style.css.bak.hyprspot.{stamp}"))
        css.write_text(css.read_text().rstrip("\n") + "\n" + STYLE_BLOCK)
        done.append("estilo")
    return ", ".join(done) or "já instalado"
