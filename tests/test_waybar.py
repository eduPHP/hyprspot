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
    assert s["class"] == "playing" and s["text"].startswith(waybar.ICON_PAUSE)
    assert "Californication · Red Hot Chili Peppers" in s["text"]
    assert waybar.status(_pb(status="paused"), False)["class"] == "paused"
    assert waybar.status(_pb(), True)["class"] == ["open", "playing"]
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
    assert data["modules-right"] == ["custom/hyprspot", "bluetooth", "cpu"]
    assert data["custom/hyprspot"]["on-click"] == "/b/hyprspot-ctl bar-toggle"
    assert waybar.add_module(new, Path("/b")) is None  # idempotente


def test_install_backs_up_and_is_idempotent(tmp_path):
    (tmp_path / "config.jsonc").write_text(CONFIG)
    (tmp_path / "style.css").write_text("* { font-size: 12px; }\n")
    assert waybar.install(Path("/b"), tmp_path) == "config, estilo"
    assert len(list(tmp_path.glob("config.jsonc.bak.hyprspot.*"))) == 1
    assert "#custom-hyprspot" in (tmp_path / "style.css").read_text()
    assert waybar.install(Path("/b"), tmp_path) == "já instalado"
