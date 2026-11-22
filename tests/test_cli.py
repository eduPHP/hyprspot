from hyprspot.cli import ACTIONS, parse_args


def test_default_is_run():
    assert parse_args([]) == ("run", {})


def test_expand_search():
    assert parse_args(["expand", "--search"]) == ("expand", {"search": True})
    assert parse_args(["expand"]) == ("expand", {"search": False})


def test_simple_commands():
    for c in ("toggle", "collapse", "hide", "show", "play-pause", "next", "prev", "login", "quit",
              "soloist-status", "soloist-update"):
        assert parse_args([c]) == (c, {})


def test_unknown_is_help():
    assert parse_args(["dance"]) == ("help", {})
    assert parse_args(["toggle", "extra"]) == ("help", {})
    assert parse_args(["--help"]) == ("help", {})


def test_version():
    assert parse_args(["--version"]) == ("version", {})


def test_remote_commands_map_to_actions():
    assert ACTIONS["toggle"] == "toggle"
    assert ACTIONS["play-pause"] == "play-pause"
    assert ACTIONS["quit"] == "quit"
    assert "login" not in ACTIONS and "run" not in ACTIONS



def test_waybar_status_kinds():
    from hyprspot.cli import parse_args
    assert parse_args(["waybar-status"]) == ("waybar-status", {"kind": "main"})
    assert parse_args(["waybar-status", "prev"]) == ("waybar-status", {"kind": "prev"})
    assert parse_args(["waybar-status", "x"])[0] == "help"
