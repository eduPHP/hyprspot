"""Argumentos de linha de comando (puro)."""
from __future__ import annotations

SIMPLE = ("toggle", "collapse", "hide", "show", "play-pause", "next", "prev", "login", "setup", "quit", "settings",
          "waybar-status", "bar-toggle", "volume-up", "volume-down",
          "soloist-status", "soloist-update")

# comandos repassados à instância em execução → nome da Gio.Action
ACTIONS = {
    "toggle": "toggle", "collapse": "collapse", "hide": "hide", "show": "show",
    "play-pause": "play-pause", "next": "next", "prev": "prev", "quit": "quit",
    "bar-toggle": "bar-toggle", "volume-up": "volume-up", "volume-down": "volume-down",
}

HELP = """uso: hyprspot [comando]

  (sem comando)     inicia o widget
  toggle            expande/recolhe
  expand [--search] expande (na busca)
  settings          expande na aba Ajustes
  bar-toggle        abre/fecha o player embaixo do cursor (o clique no item da Waybar)
  volume-up | volume-down
  waybar-status [prev|next]  o texto dos itens custom/hyprspot da Waybar (fica rodando)
  collapse          recolhe
  hide | show       esconde/mostra o widget
  quit              fecha o hyprspot (a música continua no Soloist)
  play-pause | next | prev
  setup             tela de configuração (chave do Soloist, app, login, pareamento)
  login             login na Web API do Spotify (navegador)
  soloist-status    mostra a validade do Soloist instalado
  soloist-update    baixa e instala o build mais recente do Soloist
  --version"""


def parse_args(argv: list[str]) -> tuple[str, dict]:
    if not argv:
        return "run", {}
    cmd, rest = argv[0], argv[1:]
    if cmd == "--version" and not rest:
        return "version", {}
    if cmd == "expand" and rest in ([], ["--search"]):
        return "expand", {"search": rest == ["--search"]}
    if cmd == "waybar-status" and rest in ([], ["main"], ["prev"], ["next"]):
        return cmd, {"kind": rest[0] if rest else "main"}
    if cmd in SIMPLE and not rest:
        return cmd, {}
    return "help", {}
