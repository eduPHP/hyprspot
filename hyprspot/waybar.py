"""Modo Waybar (puro): o texto do item custom/hyprspot e a instalação do item na config da Waybar.

O app grava o estado em $XDG_RUNTIME_DIR/hyprspot/waybar.json; `hyprspot waybar-status` (o `exec`
do item) acompanha esse arquivo e imprime uma linha JSON a cada mudança. Flutuando, ou com o app
fechado, o texto sai vazio e a Waybar esconde o item.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # o `exec` dos itens (stream) não pode depender do GI/Soup: importa só json
    from hyprspot.state import Playback

RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "hyprspot"
STATUS_PATH = RUNTIME / "waybar.json"
WAYBAR_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "waybar"
MODULE = "custom/hyprspot"
MAX_CHARS = 40
ICON_PLAY, ICON_PAUSE, ICON_NOTE = "\U000f040a", "\U000f03e4", "\U000f075a"  # Nerd Font (md-play, md-pause, md-music)
ICON_PREV, ICON_NEXT = "\U000f04ae", "\U000f04ad"  # md-skip-previous, md-skip-next
HIDDEN = {"text": "", "tooltip": "", "class": "hidden"}


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cut(s: str, n: int = MAX_CHARS) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def progress(pb: Playback, now_ms: int | None = None) -> int | None:
    """Posição da faixa em % inteiros (a classe pN desenha a linha de progresso embaixo do item)."""
    from hyprspot.state import clock_ms, position_now

    dur = pb.track.duration_ms if pb.track else 0
    if not dur:
        return None
    return max(0, min(100, position_now(pb, clock_ms() if now_ms is None else now_ms) * 100 // dur))


def status(pb: Playback, open_: bool, context_name: str | None = None, soloist_days: int | None = None,
           now_ms: int | None = None) -> dict:
    """O JSON do item da barra para o estado atual."""
    from hyprspot.presenter import now_playing

    v = now_playing(pb, context_name, soloist_days)
    tip = "\n".join(x for x in (v.title, v.detail or v.subtitle) if x)
    if v.offline:
        return {"text": ICON_NOTE, "tooltip": _esc(tip), "class": "disconnected"}
    if pb.track is None:
        text, cls = f"{ICON_NOTE}  {v.title}", "idle"
    else:
        # sem o ícone de tocar/pausar: ele é um item próprio (custom/hyprspot-play), porque clicar aqui
        # abre o player
        text = _esc(_cut(" · ".join(x for x in (v.title, v.subtitle) if x)))
        cls = "playing" if v.playing else "paused"
    classes = (["open"] if open_ else []) + [cls]
    pct = progress(pb, now_ms)
    if pct is not None:
        classes.append(f"p{pct}")
    return {"text": text, "tooltip": _esc(tip), "class": classes if len(classes) > 1 else cls}


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


def control(main: dict, kind: str) -> dict:
    """Os botões ao lado do item: somem com ele (flutuando, app fechado ou Soloist desconectado).
    Tocar/pausar some também sem faixa (idle)."""
    cls = main.get("class")
    classes = cls if isinstance(cls, list) else [cls]
    if not main.get("text") or "hidden" in classes or "disconnected" in classes:
        return dict(HIDDEN)
    if kind == "play":
        if "playing" in classes:
            return {"text": ICON_PAUSE, "tooltip": "Pausar", "class": ["control", "playing"]}
        if "paused" in classes:
            return {"text": ICON_PLAY, "tooltip": "Tocar", "class": ["control", "paused"]}
        return dict(HIDDEN)
    if kind == "prev":
        return {"text": ICON_PREV, "tooltip": "Faixa anterior", "class": "control"}
    return {"text": ICON_NEXT, "tooltip": "Próxima faixa", "class": "control"}


def _main(main: dict) -> dict:
    """O item principal, sem o ícone de tocar/pausar que um app de versão anterior ainda grave no começo."""
    text = main.get("text") or ""
    for icon in (ICON_PAUSE, ICON_PLAY):
        if text.startswith(icon + "  "):
            return {**main, "text": text[len(icon) + 2:]}
    return main


def stream(path: Path = STATUS_PATH, out=sys.stdout, poll_s: float = 0.25, once: bool = False,
           kind: str = "main") -> None:
    """O `exec` de cada item (main, prev, play, next): uma linha por mudança do arquivo de estado."""
    last = None
    while True:
        try:
            main = json.loads(path.read_text() or "null") or dict(HIDDEN)
        except (OSError, ValueError):
            main = dict(HIDDEN)
        line = json.dumps(_main(main) if kind == "main" else control(main, kind), ensure_ascii=False)
        if line != last:
            out.write(line + "\n")
            out.flush()
            last = line
        if once:
            return
        time.sleep(poll_s)


# ---------------------------------------------------------------------- config da Waybar
PREV, PLAY, NEXT = MODULE + "-prev", MODULE + "-play", MODULE + "-next"
MODULES = (PREV, PLAY, NEXT, MODULE)  # os botões juntos e o item (que abre o player) por último
_OURS = re.compile(r'"custom/hyprspot(?:-(?:prev|play|next))?"\s*,?\s*')


def module_block(bin_dir: Path, name: str = MODULE) -> str:
    ctl = bin_dir / "hyprspot-ctl"
    if name == MODULE:
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
    kind = {PREV: "prev", PLAY: "play", NEXT: "next"}[name]
    action = "play-pause" if kind == "play" else kind
    return f'''  "{name}": {{
    "exec": "{ctl} waybar {kind}",
    "return-type": "json",
    "restart-interval": 5,
    "tooltip": true,
    "on-click": "{ctl} media {action}"
  }},
'''


STYLE_BLOCK = """
/* custom/hyprspot (hyprspot na Waybar) */
#custom-hyprspot { padding: 0 7px; margin: 3px 2px; border-radius: 5px; }
#custom-hyprspot.paused, #custom-hyprspot.idle { color: @muted; }
#custom-hyprspot.open { background-color: alpha(@accent, 0.18); color: @accent; }
#custom-hyprspot.disconnected { background-color: alpha(@red, 0.15); color: @red; }
"""
CONTROL_STYLE = """
/* custom/hyprspot-prev e -next: anterior / próxima ao lado do item */
#custom-hyprspot-prev, #custom-hyprspot-next { padding: 0 5px; margin: 3px 0; border-radius: 5px; color: @muted; }
#custom-hyprspot-prev:hover, #custom-hyprspot-next:hover { color: @foreground; background-color: alpha(@foreground, 0.08); }
"""
PLAY_STYLE = """
/* custom/hyprspot-play: tocar / pausar, entre anterior e próxima */
#custom-hyprspot-play { padding: 0 5px; margin: 3px 0; border-radius: 5px; color: @foreground; }
#custom-hyprspot-play:hover { background-color: alpha(@foreground, 0.08); }
"""


PROGRESS_STYLE = """
/* custom/hyprspot: o fundo do item vai enchendo com a posição da faixa (classe p0…p100) */
#custom-hyprspot {
  background-image: linear-gradient(alpha(@accent, 0.22), alpha(@accent, 0.22));
  background-repeat: no-repeat; background-position: left center; background-size: 0% 100%;
}
#custom-hyprspot.paused { background-image: linear-gradient(alpha(@muted, 0.18), alpha(@muted, 0.18)); }
""" + "".join(f"#custom-hyprspot.p{i} {{ background-size: {i}% 100%; }}\n" for i in range(1, 101))


def _quoted(name: str) -> str:
    return f'"{name}"'


def add_module(config_text: str, bin_dir: Path) -> str | None:
    """Põe anterior, tocar/pausar, próxima e o item no início de modules-right (ou, se já houver algum
    deles, no lugar do primeiro, na ordem certa) e as definições que faltam antes do fim.
    None: já está tudo, ou não deu."""
    text = config_text
    i = text.find('"modules-right"')
    j = text.find("[", i) if i >= 0 else -1
    k = text.find("]", j) if j >= 0 else -1
    if k < 0:
        return None
    listed = text[j + 1:k]
    group = ", ".join(_quoted(m) for m in MODULES)
    found = _OURS.search(listed)
    if found:
        at = found.start()
        rest = _OURS.sub("", listed)
        after = rest[at:]
        if after.strip():
            new_listed = rest[:at] + group + ", " + after
        else:
            new_listed = re.sub(r",\s*$", "", rest[:at].rstrip()) + (", " if rest[:at].strip() else "") + group
    else:
        first = listed.lstrip()
        new_listed = group + (", " if first else "") + listed.lstrip(" ")
    if new_listed != listed:
        text = text[: j + 1] + new_listed + text[k:]
    missing = [m for m in MODULES if f"{_quoted(m)}:" not in text and f"{_quoted(m)} :" not in text]
    if text == config_text and not missing:
        return None
    if missing:
        end = text.rfind("}")
        before = text[:end].rstrip()
        if not before.endswith((",", "{")):
            before += ","
        blocks = "".join(module_block(bin_dir, m) for m in missing).rstrip().rstrip(",")
        text = before + "\n\n" + blocks + "\n" + text[end:]
    return text


def install(bin_dir: Path, waybar_dir: Path = WAYBAR_DIR) -> str:
    """Adiciona os itens à config e o estilo à folha da Waybar, com backup. Devolve o que fez."""
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
    if css.exists():
        old = css.read_text()
        extra = ("" if "#custom-hyprspot {" in old else STYLE_BLOCK) + \
                ("" if "#custom-hyprspot-prev" in old else CONTROL_STYLE) + \
                ("" if "#custom-hyprspot-play" in old else PLAY_STYLE) + \
                ("" if "#custom-hyprspot.p50 " in old else PROGRESS_STYLE)
        if extra:
            shutil.copy2(css, css.with_name(f"style.css.bak.hyprspot.{stamp}"))
            css.write_text(old.rstrip("\n") + "\n" + extra)
            done.append("estilo")
    return ", ".join(done) or "já instalado"
