import json

from hyprspot.config import Config, load_config, save_margin


def test_defaults_when_only_client_id(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('client_id = "x"\n')
    c = load_config(p)
    assert c.client_id == "x"
    assert c.anchor == "bottom-right"
    assert c.margin == (16, 16)
    assert c.collapse_delay_ms == 150


def test_invalid_anchor_falls_back(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('client_id = "x"\nanchor = "middle"\n')
    assert load_config(p).anchor == "bottom-right"


def test_missing_file_reads_client_id_from_token(tmp_path):
    (tmp_path / "token.json").write_text(json.dumps({"client_id": "abc"}))
    c = load_config(tmp_path / "config.toml", token_path=tmp_path / "token.json")
    assert c.client_id == "abc"


def test_save_margin_roundtrip(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('client_id = "x"\nanchor = "top-left"\n')
    save_margin(p, (40, 20))
    c = load_config(p)
    assert c.margin == (40, 20)
    assert c.client_id == "x"
    assert c.anchor == "top-left"
    assert isinstance(c, Config)


def test_save_margin_keeps_the_monitor_it_was_dropped_on(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('client_id = "x"\nmonitor = "eDP-1"\n')
    save_margin(p, (40, 20), "HDMI-A-1")
    assert load_config(p).monitor == "HDMI-A-1"
    save_margin(p, (10, 10))                       # sem monitor conhecido: não apaga o salvo
    assert load_config(p).monitor == "HDMI-A-1"
