"""Configuração do hyprspot (~/.config/hyprspot/config.toml) e caminhos padrão."""
from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "hyprspot"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "hyprspot"
CONFIG_PATH = CONFIG_DIR / "config.toml"
TOKEN_PATH = CONFIG_DIR / "token.json"

ANCHORS = ("top-left", "top-right", "bottom-left", "bottom-right")


@dataclass
class Config:
    client_id: str
    anchor: str = "bottom-right"
    margin: tuple[int, int] = (16, 16)
    monitor: str = ""
    soloist_ws: str = "ws://127.0.0.1:9090"
    collapse_delay_ms: int = 150


def _read(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def load_config(path: Path = CONFIG_PATH, token_path: Path = TOKEN_PATH) -> Config:
    raw = _read(path)
    client_id = raw.get("client_id") or ""
    if not client_id:
        try:
            client_id = json.loads(token_path.read_text()).get("client_id", "")
        except (OSError, json.JSONDecodeError):
            client_id = ""
    c = Config(client_id=client_id)
    if raw.get("anchor") in ANCHORS:
        c.anchor = raw["anchor"]
    m = raw.get("margin")
    if isinstance(m, list) and len(m) == 2 and all(isinstance(v, int) for v in m):
        c.margin = (m[0], m[1])
    if isinstance(raw.get("monitor"), str):
        c.monitor = raw["monitor"]
    if isinstance(raw.get("soloist_ws"), str):
        c.soloist_ws = raw["soloist_ws"]
    if isinstance(raw.get("collapse_delay_ms"), int):
        c.collapse_delay_ms = raw["collapse_delay_ms"]
    return c


def _toml_value(v) -> str:
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, tuple):
        return "[" + ", ".join(str(x) for x in v) + "]"
    return str(v)


def save_config(path: Path, c: Config) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{f.name} = {_toml_value(getattr(c, f.name))}" for f in fields(c)]
    path.write_text("\n".join(lines) + "\n")


def save_client_id(path: Path, client_id: str, token_path: Path = TOKEN_PATH) -> None:
    c = load_config(path, token_path)
    c.client_id = client_id.strip().lower()
    save_config(path, c)


def save_margin(path: Path, margin: tuple[int, int], monitor: str | None = None,
                token_path: Path = TOKEN_PATH) -> None:
    """Salva a posição; `monitor` (conector, ex. "HDMI-A-1") só muda quando informado."""
    c = load_config(path, token_path)
    c.margin = (int(margin[0]), int(margin[1]))
    if monitor:
        c.monitor = monitor
    save_config(path, c)
