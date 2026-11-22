"""Configuração inicial (puro): chave do Soloist, client_id da Web API e build por arquitetura."""
from __future__ import annotations

import os
import re
from pathlib import Path

from hyprspot.config import CONFIG_DIR

SOLOIST_ENV = CONFIG_DIR / "soloist.env"
SOLOIST_KEY_URL = "https://developer.spotify.com/dashboard/soloist"
DASHBOARD_CREATE_URL = "https://developer.spotify.com/dashboard/create"
_BUILD = "https://soloist-builds.spotifycdn.com/soloist_release_{}.tar.gz"
_ARCH = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}
_CLIENT_ID = re.compile(r"[0-9a-f]{32}")
RESTART_SOLOIST = ("systemctl --user daemon-reload; systemctl --user enable soloist.service;"
                   " systemctl --user restart soloist.service")
_KEY = re.compile(r"[A-Za-z0-9._~+/=:-]+")


def build_url(machine: str) -> str | None:
    """URL do build oficial do Soloist para `platform.machine()`; None se não há build."""
    arch = _ARCH.get(machine.lower())
    return _BUILD.format(arch) if arch else None


def valid_client_id(text: str) -> bool:
    return bool(_CLIENT_ID.fullmatch(text.strip().lower()))


def clean_key(text: str) -> str | None:
    """Chave colada pelo usuário → valor limpo, ou None se não parece uma chave."""
    k = text.strip()
    if k.startswith("SOLOIST_API_KEY="):
        k = k.split("=", 1)[1].strip()
    return k if k and _KEY.fullmatch(k) else None


def write_soloist_env(key: str, path: Path = SOLOIST_ENV) -> None:
    """Grava SOLOIST_API_KEY=<key> com permissão 600, de forma atômica."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"SOLOIST_API_KEY={key}\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def read_key(path: Path = SOLOIST_ENV) -> str | None:
    try:
        text = path.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("SOLOIST_API_KEY="):
            return line.split("=", 1)[1].strip() or None
    return None


def has_key(path: Path = SOLOIST_ENV) -> bool:
    return read_key(path) is not None


def parse_ctl_status(text: str) -> dict:
    """Saída de `soloist ctl status` → {running, logged_in, active}."""
    t = text.lower()
    return {
        "running": bool(re.search(r"^soloist:\s*running", t, re.M)),
        "logged_in": "logged in: yes" in t,
        "active": "active device: yes" in t,
    }
