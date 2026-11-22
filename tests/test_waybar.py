import io
import json
import re
from pathlib import Path

from hyprspot.model import Track
from hyprspot.state import Playback
from hyprspot import waybar


def _pb(**kw):
    base = dict(connected=True, logged_in=True, status="playing",
                track=Track(uri="spotify:track:1", title="Californication", artist="Red Hot Chili Peppers",
                            duration_ms=329000))
    base.update(kw)
    return Playback(**base)


def test_status_playing_paused_open_and_offline():
    s = waybar.status(_pb(), False)
    assert s["class"][0] == "playing" and s["text"].startswith(waybar.ICON_PAUSE)
    assert "Californication · Red Hot Chili Peppers" in s["text"]
    assert waybar.status(_pb(status="paused"), False)["class"] == ["paused", "p0"]
    assert waybar.status(_pb(), True)["class"][:2] == ["open", "playing"]
    off = waybar.status(Playback(), False)
    assert off["class"] == "disconnected" and off["text"] == waybar.ICON_NOTE


def test_status_cuts_long_lines_and_escapes_markup():
    t = Track(uri="spotify:track:1", title="A & B <live> " * 5, artist="X", duration_ms=1)
    text = waybar.status(_pb(track=t), False)["text"]
    assert "&amp;" in text and "<live>" not in text and "…" in text


def test_write_status_only_when_changed_and_stream(tmp_path):
    p = tmp_path / "waybar.json"
    assert waybar.write_status({"text": "a"}, p) is True
    assert waybar.write_status({"text": "a"}, p) is False
    out = io.StringIO()
    waybar.stream(p, out, once=True)
    assert json.loads(out.getvalue()) == {"text": "a"}
    waybar.write_status(None, p)
    out = io.StringIO()
    waybar.stream(p, out, once=True)
    assert json.loads(out.getvalue())["text"] == ""


CONFIG = """{
  "layer": "top",
  // comentário
  "modules-right": ["bluetooth", "cpu"],
  "cpu": { "interval": 5 }
}
"""


def _jsonc(text):
    return json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.M))


def test_add_module_puts_it_first_on_the_right_and_stays_valid():
    new = waybar.add_module(CONFIG, Path("/b"))
    data = _jsonc(new)
    assert data["modules-right"] == ["custom/hyprspot-prev", "custom/hyprspot", "custom/hyprspot-next", "bluetooth", "cpu"]
    assert data["custom/hyprspot"]["on-click"] == "/b/hyprspot-ctl bar-toggle"
    assert data["custom/hyprspot-prev"]["on-click"] == "/b/hyprspot-ctl media prev"
    assert data["custom/hyprspot-next"]["exec"] == "/b/hyprspot-ctl waybar next"
    assert waybar.add_module(new, Path("/b")) is None  # idempotente


def test_install_backs_up_and_is_idempotent(tmp_path):
    (tmp_path / "config.jsonc").write_text(CONFIG)
    (tmp_path / "style.css").write_text("* { font-size: 12px; }\n")
    assert waybar.install(Path("/b"), tmp_path) == "config, estilo"
    assert len(list(tmp_path.glob("config.jsonc.bak.hyprspot.*"))) == 1
    assert "#custom-hyprspot" in (tmp_path / "style.css").read_text()
    assert waybar.install(Path("/b"), tmp_path) == "já instalado"


OLD_INSTALL = """{
  "modules-right": ["custom/hyprspot", "bluetooth"],
  "custom/hyprspot": { "exec": "x" }
}
"""


def test_upgrade_wraps_the_old_item_with_prev_and_next():
    data = _jsonc(waybar.add_module(OLD_INSTALL, Path("/b")))
    assert data["modules-right"] == ["custom/hyprspot-prev", "custom/hyprspot", "custom/hyprspot-next", "bluetooth"]
    assert data["custom/hyprspot"] == {"exec": "x"}  # a definição antiga fica como está
    assert "custom/hyprspot-next" in data


def test_controls_follow_the_main_item():
    out = io.StringIO()
    assert waybar.control({"text": "x", "class": "playing"}, "prev")["text"] == waybar.ICON_PREV
    assert waybar.control({"text": "x", "class": ["open", "paused"]}, "next")["text"] == waybar.ICON_NEXT
    assert waybar.control(dict(waybar.HIDDEN), "next")["text"] == ""
    assert waybar.control({"text": waybar.ICON_NOTE, "class": "disconnected"}, "prev")["text"] == ""


def test_progress_class_follows_the_position():
    pb = _pb(status="playing", position_ms=164500, position_ts_ms=1000, speed=1.0)
    assert waybar.status(pb, False, now_ms=1000)["class"] == ["playing", "p50"]
    assert waybar.status(pb, False, now_ms=1000 + 33000)["class"] == ["playing", "p60"]
    assert waybar.progress(_pb(track=None)) is None
    assert "#custom-hyprspot.p100 { background-size: 100% 100%; }" in waybar.PROGRESS_STYLE
