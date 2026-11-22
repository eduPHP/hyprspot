import sys

from hyprspot import __version__
from hyprspot.cli import HELP, parse_args


def main(argv: list[str]) -> int:
    cmd, opts = parse_args(argv)
    if cmd == "version":
        print(f"hyprspot {__version__}")
        return 0
    if cmd == "help":
        print(HELP)
        return 0 if argv[:1] in (["--help"], ["-h"]) else 2
    if cmd == "login":
        from hyprspot.auth import TokenStore, run_login
        from hyprspot.config import TOKEN_PATH, load_config

        cid = load_config().client_id
        if not cid:
            print("Defina client_id em ~/.config/hyprspot/config.toml (app do dashboard do Spotify).")
            return 1
        run_login(cid, TokenStore(TOKEN_PATH))
        return 0
    if cmd == "setup":
        from hyprspot.ui.setup_window import run as run_setup

        return run_setup()
    if cmd == "waybar-status":
        from hyprspot.waybar import stream

        try:
            stream(kind=opts.get("kind", "main"))
        except (KeyboardInterrupt, BrokenPipeError):
            pass
        return 0
    if cmd == "soloist-status":
        from hyprspot.soloistctl import run_status

        return run_status()
    if cmd == "soloist-update":
        from hyprspot.soloistctl import run_update

        return run_update()
    from hyprspot.app import run

    return run([sys.argv[0], *argv])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
