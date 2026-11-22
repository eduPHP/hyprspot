"""install.sh num HOME temporário, com hyprctl/systemctl falsos."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HYPRLAND = "source = ~/.config/hypr/monitors.conf\nbind = SUPER, Q, killactive\n"


def run_install(home: Path, *args):
    fake = home / "fakebin"
    fake.mkdir(exist_ok=True)
    for tool in ("hyprctl", "systemctl"):
        (fake / tool).write_text("#!/bin/sh\nexit 0\n")
        (fake / tool).chmod(0o755)
    hypr = home / ".config" / "hypr"
    hypr.mkdir(parents=True, exist_ok=True)
    if not (hypr / "hyprland.conf").exists():
        (hypr / "hyprland.conf").write_text(HYPRLAND)
    env = {**os.environ, "HOME": str(home), "PATH": f"{fake}:{os.environ['PATH']}"}
    return subprocess.run([str(ROOT / "install.sh"), *args], env=env, capture_output=True, text=True)


def unit(home: Path) -> Path:
    p = home / ".config" / "systemd" / "user" / "spotifyd.service"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def test_foreign_spotifyd_unit_is_kept(tmp_path):
    u = unit(tmp_path)
    u.write_text("[Unit]\nDescription=My own spotifyd\n")
    r = run_install(tmp_path)
    assert r.returncode == 0, r.stderr
    assert u.read_text() == "[Unit]\nDescription=My own spotifyd\n"


def test_hyprspot_spotifyd_unit_is_removed(tmp_path):
    u = unit(tmp_path)
    u.write_text("[Unit]\nDescription=spotifyd (player do hyprspot)\n")
    r = run_install(tmp_path)
    assert r.returncode == 0, r.stderr
    assert not u.exists()


def hypr(home: Path) -> Path:
    return home / ".config" / "hypr"


def test_install_writes_own_conf_and_sources_it(tmp_path):
    r = run_install(tmp_path)
    assert r.returncode == 0, r.stderr
    conf = (hypr(tmp_path) / "hyprspot.conf").read_text()
    assert f"exec, {tmp_path}/.local/bin/hyprspot-ctl toggle" in conf
    assert f"exec-once = {tmp_path}/.local/bin/hyprspot" in conf
    assert r"match:class ^(dev\.hyprspot\.Setup)$, float on" in conf
    # sem ignore_alpha o blur pinta o retângulo inteiro, e os cantos arredondados ficam cinza
    assert "layerrule = match:namespace hyprspot, blur on, ignore_alpha 0.2, no_anim on" in conf
    # no_anim: ao expandir/recolher o Hyprland animava a troca de tamanho esticando a imagem
    # antiga (a barra de progresso aparecia gigante por um instante)
    # as teclas de mídia e de volume ficam com o sistema: o hyprspot não desfaz nem refaz esses atalhos
    assert "XF86Audio" not in conf and "unbind" not in conf
    main = (hypr(tmp_path) / "hyprland.conf").read_text()
    assert main.startswith(HYPRLAND)
    assert f"source = {tmp_path}/.config/hypr/hyprspot.conf" in main
    assert not (hypr(tmp_path) / "bindings.conf").exists()


def test_install_is_idempotent(tmp_path):
    run_install(tmp_path)
    before = (hypr(tmp_path) / "hyprland.conf").read_text()
    r = run_install(tmp_path)
    assert r.returncode == 0
    assert (hypr(tmp_path) / "hyprland.conf").read_text() == before
    assert "hyprland.conf" not in r.stdout


def test_install_then_remove_restores_hyprland_conf(tmp_path):
    assert run_install(tmp_path).returncode == 0
    assert run_install(tmp_path, "--remove").returncode == 0
    assert (hypr(tmp_path) / "hyprland.conf").read_text() == HYPRLAND
    assert not (hypr(tmp_path) / "hyprspot.conf").exists()
    assert not (tmp_path / ".local" / "bin" / "hyprspot").exists()


def test_legacy_blocks_are_migrated(tmp_path):
    b = hypr(tmp_path) / "bindings.conf"
    b.parent.mkdir(parents=True)
    b.write_text("bind = SUPER, Q, killactive\n\n# >>> hyprspot (gerado por install.sh, não editar à mão)\n"
                 "bindd = SUPER ALT, M, hyprspot, exec, hyprspot-ctl toggle\n# <<< hyprspot\n")
    assert run_install(tmp_path).returncode == 0
    assert b.read_text() == "bind = SUPER, Q, killactive\n"


def test_missing_hyprland_conf_fails(tmp_path):
    h = hypr(tmp_path)
    h.mkdir(parents=True)
    (h / "hyprland.conf").write_text("")
    (h / "hyprland.conf").unlink()
    fake = tmp_path / "fakebin"
    fake.mkdir()
    for tool in ("hyprctl", "systemctl"):
        (fake / tool).write_text("#!/bin/sh\nexit 0\n")
        (fake / tool).chmod(0o755)
    env = {**os.environ, "HOME": str(tmp_path), "PATH": f"{fake}:{os.environ['PATH']}"}
    r = subprocess.run([str(ROOT / "install.sh")], env=env, capture_output=True, text=True)
    assert r.returncode == 1 and "hyprland.conf" in r.stderr


def soloist_unit(home: Path) -> Path:
    return home / ".config" / "systemd" / "user" / "soloist.service"


def test_soloist_unit_installed_from_template(tmp_path):
    assert run_install(tmp_path).returncode == 0
    assert soloist_unit(tmp_path).read_text() == (ROOT / "share" / "soloist.service").read_text()


def test_foreign_soloist_unit_is_kept(tmp_path):
    u = soloist_unit(tmp_path)
    u.parent.mkdir(parents=True)
    u.write_text("[Unit]\nDescription=meu soloist\n")
    r = run_install(tmp_path)
    assert r.returncode == 0 and u.read_text() == "[Unit]\nDescription=meu soloist\n"
    assert "mantido" in r.stdout
