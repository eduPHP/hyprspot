import stat

from hyprspot import onboarding as ob
from hyprspot.config import load_config, save_client_id


def test_build_url_per_arch():
    assert ob.build_url("x86_64").endswith("soloist_release_x86_64.tar.gz")
    assert ob.build_url("amd64").endswith("soloist_release_x86_64.tar.gz")
    assert ob.build_url("aarch64").endswith("soloist_release_arm64.tar.gz")
    assert ob.build_url("armv7l") is None


def test_valid_client_id():
    assert ob.valid_client_id("3f9a2c7e51d84b06a8e2f41c97d05b6e")
    assert ob.valid_client_id("  3F9A2C7E51D84B06A8E2F41C97D05B6E \n")
    assert not ob.valid_client_id("3f9a2c7e51d84b06a8e2f41c97d05b6")
    assert not ob.valid_client_id("http://127.0.0.1:8898/callback")
    assert not ob.valid_client_id("")


def test_clean_key():
    assert ob.clean_key("  abc123-XYZ_9 \n") == "abc123-XYZ_9"
    assert ob.clean_key("SOLOIST_API_KEY=abc123") == "abc123"
    assert ob.clean_key("") is None
    assert ob.clean_key("abc def") is None
    assert ob.clean_key('abc"') is None


def test_write_soloist_env_is_private(tmp_path):
    p = tmp_path / "hs" / "soloist.env"
    ob.write_soloist_env("k3y", p)
    assert p.read_text() == "SOLOIST_API_KEY=k3y\n"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert ob.has_key(p)
    ob.write_soloist_env("outra", p)
    assert p.read_text() == "SOLOIST_API_KEY=outra\n"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_has_key_false_when_missing_or_empty(tmp_path):
    p = tmp_path / "soloist.env"
    assert not ob.has_key(p)
    p.write_text("SOLOIST_API_KEY=\n")
    assert not ob.has_key(p)


def test_parse_ctl_status():
    text = "soloist: running (pid 1)\n  ws: listening on 127.0.0.1:9090\n  logged in: yes\n  active device: no\n"
    assert ob.parse_ctl_status(text) == {"running": True, "logged_in": True, "active": False}
    assert ob.parse_ctl_status("soloist: not running\n") == {"running": False, "logged_in": False, "active": False}
    assert ob.parse_ctl_status("") == {"running": False, "logged_in": False, "active": False}


def test_save_client_id_keeps_other_settings(tmp_path):
    cfg, tok = tmp_path / "config.toml", tmp_path / "token.json"
    cfg.write_text('client_id = "old"\nanchor = "top-left"\nmargin = [5, 7]\n')
    save_client_id(cfg, " 3F9A2C7E51D84B06A8E2F41C97D05B6E ", tok)
    c = load_config(cfg, tok)
    assert c.client_id == "3f9a2c7e51d84b06a8e2f41c97d05b6e"
    assert c.anchor == "top-left" and c.margin == (5, 7)


def test_save_client_id_creates_file(tmp_path):
    cfg = tmp_path / "new" / "config.toml"
    save_client_id(cfg, "3f9a2c7e51d84b06a8e2f41c97d05b6e", tmp_path / "token.json")
    assert load_config(cfg, tmp_path / "token.json").client_id == "3f9a2c7e51d84b06a8e2f41c97d05b6e"


def test_read_key(tmp_path):
    from hyprspot.onboarding import read_key, write_soloist_env
    p = tmp_path / "soloist.env"
    assert read_key(p) is None
    write_soloist_env("spak_abc", p)
    assert read_key(p) == "spak_abc"
    p.write_text("SOLOIST_API_KEY=\n")
    assert read_key(p) is None
