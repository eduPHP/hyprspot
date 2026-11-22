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


def test_opacity_defaults_and_is_clamped(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('client_id = "x"\n')
    assert load_config(p).opacity == 0.94
    p.write_text('client_id = "x"\nopacity = 0.05\n')
    assert load_config(p).opacity == 0.3
    p.write_text('client_id = "x"\nopacity = 1\n')
    assert load_config(p).opacity == 1.0
    p.write_text('client_id = "x"\nopacity = "meio"\n')
    assert load_config(p).opacity == 0.94


def test_update_config_changes_only_the_given_fields(tmp_path):
    from hyprspot.config import update_config
    p = tmp_path / "config.toml"
    p.write_text('client_id = "x"\nmargin = [40, 20]\nmonitor = "HDMI-A-1"\n')
    update_config(p, opacity=0.55, anchor="top-left")
    c = load_config(p)
    assert (c.opacity, c.anchor, c.margin, c.monitor, c.client_id) == (0.55, "top-left", (40, 20), "HDMI-A-1", "x")
    assert "opacity = 0.55\n" in p.read_text()


def test_blur_roundtrip(tmp_path):
    from hyprspot.config import update_config
    p = tmp_path / "config.toml"
    p.write_text('client_id = "x"\n')
    assert load_config(p).blur is True
    update_config(p, blur=False)
    assert "blur = false\n" in p.read_text()
    assert load_config(p).blur is False
