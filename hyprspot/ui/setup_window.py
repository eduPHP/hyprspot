"""Tela de configuração inicial (`hyprspot setup`): chave do Soloist, app da Web API, login e pareamento."""
from __future__ import annotations

import errno
import subprocess
import threading
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from hyprspot import onboarding as ob  # noqa: E402
from hyprspot.auth import REDIRECT_URI, TokenStore, run_login  # noqa: E402
from hyprspot.config import CONFIG_PATH, TOKEN_PATH, load_config, save_client_id  # noqa: E402

APP_ID = "dev.hyprspot.Setup"
CSS_PATH = Path(__file__).with_name("setup.css")
ROOT = Path(__file__).resolve().parents[2]
SOLOIST_BIN = Path.home() / ".local" / "bin" / "soloist"
POLL_MS = 2000
PAGES = ("welcome", "key", "app", "login", "pair", "done")
SHORTCUTS = (
    ("Super Alt M", "expandir / recolher"),
    ("Super Alt B", "abrir direto na busca"),
    ("Super Alt H", "esconder / mostrar"),
    ("q", "fechar (mouse parado sobre o widget)"),
    ("rolar", "volume, sobre o widget compacto"),
    ("botão do meio", "arrastar para mover o widget"),
)


class SystemActions:
    """Efeitos colaterais da tela. Os testes trocam por uma versão falsa."""

    def open_uri(self, uri: str, parent: Gtk.Window) -> None:
        Gtk.UriLauncher.new(uri).launch(parent, None, None, None)

    def copy(self, text: str) -> None:
        Gdk.Display.get_default().get_clipboard().set(text)

    def has_key(self) -> bool:
        return ob.has_key()

    def save_key(self, key: str) -> None:
        ob.write_soloist_env(key)
        subprocess.Popen(["sh", "-c", "systemctl --user daemon-reload; systemctl --user enable soloist.service;"
                          " systemctl --user restart soloist.service"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def client_id(self) -> str:
        return load_config().client_id

    def save_client_id(self, cid: str) -> None:
        save_client_id(CONFIG_PATH, cid)

    def has_token(self, cid: str) -> bool:
        tok = TokenStore(TOKEN_PATH).load()
        return bool(tok and tok.get("refresh_token") and tok.get("client_id") == cid)

    def login(self, cid: str, done: Callable[[str | None], None]) -> None:
        def work():
            err = None
            try:
                run_login(cid, TokenStore(TOKEN_PATH))
            except OSError as e:
                err = ("A porta 8898 está ocupada por outro programa." if e.errno == errno.EADDRINUSE
                       else f"Falhou: {e}")
            except SystemExit as e:
                err = str(e) or "O login falhou."
            except Exception as e:  # noqa: BLE001 — qualquer falha vira mensagem na tela
                err = f"Falhou: {e}"
            GLib.idle_add(lambda: (done(err), False)[1])

        threading.Thread(target=work, daemon=True).start()

    def soloist_status(self, cb: Callable[[dict], None]) -> None:
        if not SOLOIST_BIN.exists():
            cb(ob.parse_ctl_status(""))
            return
        proc = Gio.Subprocess.new([str(SOLOIST_BIN), "ctl", "status"],
                                  Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)

        def fin(p, res):
            try:
                _ok, out, _err = p.communicate_utf8_finish(res)
            except GLib.Error:
                out = ""
            cb(ob.parse_ctl_status(out or ""))

        proc.communicate_utf8_async(None, None, fin)

    def launch_hyprspot(self) -> None:
        subprocess.Popen([str(ROOT / "bin" / "hyprspot")], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _label(text: str, *classes: str, markup: bool = False, selectable: bool = False) -> Gtk.Label:
    lab = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=selectable)
    if markup:
        lab.set_markup(text)
    else:
        lab.set_text(text)
    for c in classes:
        lab.add_css_class(c)
    return lab


def _button(text: str, on_click: Callable[[], None], *classes: str) -> Gtk.Button:
    b = Gtk.Button(label=text)
    for c in classes:
        b.add_css_class(c)
    b.connect("clicked", lambda *_: on_click())
    return b


def _esc(text: str) -> str:
    return GLib.markup_escape_text(text)


class SetupWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application | None, actions):
        super().__init__(application=app, title="Configurar o hyprspot")
        self.a = actions
        self.idx = 0
        self._poll_id = 0
        self._polling = False
        self._paired = False
        self._login_busy = False
        self.set_default_size(580, 600)
        self.add_css_class("hs-setup")

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(root)

        top = Gtk.Box(spacing=6)
        top.add_css_class("hs-top")
        self.dots = []
        for _ in range(4):
            d = Gtk.Box(valign=Gtk.Align.CENTER)
            d.add_css_class("hs-dot")
            self.dots.append(d)
            top.append(d)
        self.step_label = _label("", "hs-steplabel")
        self.step_label.set_hexpand(True)
        self.step_label.set_xalign(1)
        top.append(self.step_label)
        root.append(top)

        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
                               transition_duration=180)
        root.append(self.stack)

        foot = Gtk.Box(spacing=10)
        foot.add_css_class("hs-footer")
        self.back_btn = _button("Voltar", self.go_back, "hs-ghost")
        self.error = _label("", "hs-error")
        self.error.set_hexpand(True)
        self.next_btn = _button("Começar", self.go_next, "hs-primary")
        foot.append(self.back_btn)
        foot.append(self.error)
        foot.append(self.next_btn)
        root.append(foot)

        self._build_welcome()
        self._build_key()
        self._build_app()
        self._build_login()
        self._build_pair()
        self._build_done()
        self.connect("close-request", lambda *_: (self._stop_poll(), False)[1])
        self._show(0)

    # ------------------------------------------------------------------ construção
    def _page(self, name: str, title: str, intro: str, *rows: Gtk.Widget) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.add_css_class("hs-page")
        box.append(_label(title, "hs-h1"))
        if intro:
            box.append(_label(intro, "hs-intro", markup=True))
        for r in rows:
            box.append(r)
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        sc.set_child(box)
        self.stack.add_named(sc, name)

    @staticmethod
    def _step(n: int, markup: str, *extra: Gtk.Widget) -> Gtk.Widget:
        row = Gtk.Box(spacing=12)
        num = Gtk.Label(label=str(n), valign=Gtk.Align.START)
        num.add_css_class("hs-num")
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        col.append(_label(markup, "hs-stepbody", markup=True))
        for e in extra:
            col.append(e)
        row.append(num)
        row.append(col)
        return row

    def _link(self, text: str, uri: str) -> Gtk.Widget:
        b = _button(f"{text}  ↗", lambda: self.a.open_uri(uri, self), "hs-link")
        b.set_halign(Gtk.Align.START)
        b.set_tooltip_text(uri)
        return b

    def _build_welcome(self) -> None:
        need = _label("• conta <b>Spotify Premium</b>\n"
                      "• o app do Spotify no celular, no mesmo Wi-Fi (só para o primeiro pareamento)\n"
                      "• uns 5 minutos", "hs-stepbody", markup=True)
        why = _label("<b>Por que duas configurações?</b> O som sai pelo <b>Spotify Soloist</b>, o player oficial "
                     "do Spotify sem janela, que pede uma chave sua. Busca e playlists vêm da <b>Web API</b>, que "
                     "pede um app seu no painel de desenvolvedor. As duas são gratuitas e ficam só neste "
                     "computador.", "hs-note", markup=True)
        self._page("welcome", "Configurar o hyprspot",
                   "Vamos ligar o mini player à sua conta do Spotify em 4 passos. Você vai precisar de:",
                   need, why)

    def _build_key(self) -> None:
        self.key_entry = Gtk.PasswordEntry(show_peek_icon=True, hexpand=True)
        self.key_entry.set_property("placeholder-text", "Cole a chave aqui")
        self.key_entry.connect("activate", lambda *_: self.go_next())
        self.key_status = _label("", "hs-status")
        note = _label("A chave fica só neste computador, em <tt>~/.config/hyprspot/soloist.env</tt> "
                      "(permissão 600). Não compartilhe: cada pessoa gera a sua.", "hs-note", markup=True)
        self._page("key", "Chave do Soloist",
                   "O Soloist é o player oficial do Spotify que toca o som. Ele precisa de uma chave "
                   "gerada na sua conta.",
                   self._step(1, "Abra a página da chave do Soloist e entre com a sua conta <b>Premium</b>.",
                              self._link("Abrir a página da chave", ob.SOLOIST_KEY_URL)),
                   self._step(2, "Gere uma chave nessa página e copie o valor."),
                   self._step(3, "Cole a chave aqui:", self.key_entry, self.key_status),
                   note)

    def _build_app(self) -> None:
        uri_row = Gtk.Box(spacing=8)
        uri = _label(REDIRECT_URI, "hs-code", selectable=True)
        uri.set_hexpand(True)
        self.copy_btn = _button("Copiar", self._copy_uri, "hs-ghost")
        uri_row.append(uri)
        uri_row.append(self.copy_btn)
        self.cid_entry = Gtk.Entry(hexpand=True, placeholder_text="32 letras e números")
        self.cid_entry.connect("activate", lambda *_: self.go_next())
        self._page("app", "App da Web API",
                   "A Web API traz suas playlists e a busca. Para usá-la, crie um app (só seu) no painel "
                   "de desenvolvedor.",
                   self._step(1, "Abra o painel e crie um app.", self._link("Criar app no painel",
                                                                           ob.DASHBOARD_CREATE_URL)),
                   self._step(2, "Em <b>App name</b> escreva <tt>hyprspot</tt>. Em <b>App description</b>, "
                                 "qualquer texto."),
                   self._step(3, "Em <b>Redirect URIs</b> cole este endereço e clique <b>Add</b>:", uri_row),
                   self._step(4, "Em <b>Which API/SDKs are you planning to use?</b> marque <b>Web API</b>, "
                                 "aceite os termos e clique <b>Save</b>."),
                   self._step(5, "Na página do app, abra <b>Settings</b>, copie o <b>Client ID</b> "
                                 "(não o <i>Client secret</i>) e cole aqui:", self.cid_entry))

    def _build_login(self) -> None:
        self.login_btn = _button("Entrar com o Spotify", self._login, "hs-primary")
        self.login_btn.set_halign(Gtk.Align.START)
        self.login_status = _label("", "hs-status")
        tip = _label("Se o Spotify mostrar <b>INVALID_CLIENT: Invalid redirect URI</b>, volte um passo e confira "
                     f"se o endereço <tt>{_esc(REDIRECT_URI)}</tt> foi adicionado e salvo no app.",
                     "hs-note", markup=True)
        self._page("login", "Entrar no Spotify",
                   "Autorize o hyprspot a ler suas playlists, buscar músicas e curtir faixas. "
                   "O navegador vai abrir; depois de aceitar, volte para esta janela.",
                   self.login_btn, self.login_status, tip)

    def _build_pair(self) -> None:
        status = Gtk.Box(spacing=10)
        self.pair_spinner = Gtk.Spinner()
        self.pair_status = _label("Verificando o Soloist…", "hs-status")
        status.append(self.pair_spinner)
        status.append(self.pair_status)
        self._page("pair", "Parear o player",
                   "Falta só escolher o hyprspot como aparelho, <b>uma única vez</b>. Depois disso o "
                   "Soloist entra sozinho e o celular não é mais necessário.",
                   self._step(1, "Abra o Spotify no celular, conectado ao <b>mesmo Wi-Fi</b> deste computador."),
                   self._step(2, "Toque numa música e depois no ícone de aparelhos (alto-falante), "
                                 "embaixo do player."),
                   self._step(3, "Escolha <b>hyprspot</b>."),
                   status)

    def _build_done(self) -> None:
        grid = Gtk.Grid(column_spacing=14, row_spacing=10)
        for i, (keys, what) in enumerate(SHORTCUTS):
            k = Gtk.Label(label=keys, xalign=0)
            k.add_css_class("hs-key")
            k.set_halign(Gtk.Align.START)
            grid.attach(k, 0, i, 1, 1)
            grid.attach(_label(what, "hs-stepbody"), 1, i, 1, 1)
        note = _label("O Spotify faz cada build do Soloist expirar em 90 dias. Faltando 14 dias o hyprspot "
                      "avisa; para renovar, rode <tt>hyprspot soloist-update</tt>.", "hs-note", markup=True)
        self._page("done", "Tudo pronto",
                   "O hyprspot fica no canto da tela, sempre por cima, e abre sozinho quando você entra no "
                   "Hyprland. Atalhos:", grid, note)

    # ------------------------------------------------------------------ navegação
    def _show(self, idx: int) -> None:
        self.idx = idx
        name = PAGES[idx]
        self.stack.set_visible_child_name(name)
        self.error.set_text("")
        self.back_btn.set_visible(0 < idx)
        for i, d in enumerate(self.dots):
            step = i + 1
            for c in ("on", "done"):
                d.remove_css_class(c)
            if step < idx or name == "done":
                d.add_css_class("done")
            elif step == idx:
                d.add_css_class("on")
        self.step_label.set_text({"welcome": "Boas-vindas", "done": "Concluído"}.get(name, f"Passo {idx} de 4"))
        self.next_btn.set_label({"welcome": "Começar", "done": "Abrir o hyprspot"}.get(name, "Próximo"))
        if name == "key":
            self.key_status.set_text("✓ Já existe uma chave salva. Cole outra só se quiser trocar."
                                     if self.a.has_key() else "")
            self._toggle(self.key_status, "hs-ok", self.a.has_key())
            self.key_entry.grab_focus()
        elif name == "app":
            if not self.cid_entry.get_text():
                self.cid_entry.set_text(self.a.client_id())
            self.cid_entry.grab_focus()
        elif name == "login":
            self._render_login()
        if name == "pair":
            self._start_poll()
        else:
            self._stop_poll()

    def go_back(self) -> None:
        if self.idx > 0:
            self._show(self.idx - 1)

    def go_next(self) -> None:
        name = PAGES[self.idx]
        check = getattr(self, f"_leave_{name}", None)
        if check is not None:
            err = check()
            if err:
                self.error.set_text(err)
                return
        if name == "done":
            self._stop_poll()
            self.a.launch_hyprspot()
            self.close()
            return
        self._show(self.idx + 1)

    def _leave_key(self) -> str | None:
        text = self.key_entry.get_text()
        if text:
            key = ob.clean_key(text)
            if key is None:
                return "Isso não parece uma chave do Soloist."
            self.a.save_key(key)
            self.key_entry.set_text("")
            return None
        return None if self.a.has_key() else "Cole a chave para continuar."

    def _leave_app(self) -> str | None:
        cid = self.cid_entry.get_text().strip().lower()
        if not ob.valid_client_id(cid):
            return "O Client ID tem 32 caracteres (0–9, a–f)."
        if cid != self.a.client_id():
            self.a.save_client_id(cid)
        return None

    def _leave_login(self) -> str | None:
        return None if self.a.has_token(self.a.client_id()) else "Entre com o Spotify para continuar."

    # ------------------------------------------------------------------ ações
    def _copy_uri(self) -> None:
        self.a.copy(REDIRECT_URI)
        self.copy_btn.set_label("Copiado ✓")
        GLib.timeout_add(1500, lambda: (self.copy_btn.set_label("Copiar"), False)[1])

    def _render_login(self) -> None:
        if self._login_busy:
            return
        ok = self.a.has_token(self.a.client_id())
        self.login_status.set_text("✓ Conectado ao Spotify." if ok else "")
        self._toggle(self.login_status, "hs-ok", ok)
        self.login_btn.set_label("Entrar de novo" if ok else "Entrar com o Spotify")

    def _login(self) -> None:
        if self._login_busy:
            return
        self._login_busy = True
        self.login_btn.set_sensitive(False)
        self.login_status.set_text("Abrindo o navegador… autorize o hyprspot e volte para cá.")
        self._toggle(self.login_status, "hs-ok", False)
        self.error.set_text("")

        def done(err: str | None) -> None:
            self._login_busy = False
            self.login_btn.set_sensitive(True)
            if err:
                self.login_status.set_text("")
                self.error.set_text(err)
            self._render_login()

        self.a.login(self.a.client_id(), done)

    def _start_poll(self) -> None:
        self.pair_spinner.start()
        self._poll()
        if not self._poll_id:
            self._poll_id = GLib.timeout_add(POLL_MS, lambda: (self._poll(), True)[1])

    def _stop_poll(self) -> None:
        if self._poll_id:
            GLib.source_remove(self._poll_id)
            self._poll_id = 0
        self.pair_spinner.stop()

    def _poll(self) -> None:
        if self._polling:
            return
        self._polling = True
        self.a.soloist_status(self._on_status)

    def _on_status(self, st: dict) -> None:
        self._polling = False
        self._paired = st["running"] and st["logged_in"]
        if self._paired:
            msg = "✓ Pareado. O Soloist já está na sua conta."
        elif st["running"]:
            msg = "Aguardando você escolher hyprspot no celular…"
        else:
            msg = ("O Soloist não está rodando. Volte e confira a chave, ou veja o log com "
                   "journalctl --user -u soloist")
        self.pair_status.set_text(msg)
        self._toggle(self.pair_status, "hs-ok", self._paired)
        self.pair_spinner.set_visible(not self._paired)
        if PAGES[self.idx] == "pair":
            self.next_btn.set_label("Próximo" if self._paired else "Pular por agora")

    @staticmethod
    def _toggle(w: Gtk.Widget, cls: str, on: bool) -> None:
        (w.add_css_class if on else w.remove_css_class)(cls)


class SetupApp(Gtk.Application):
    def __init__(self, actions=None):
        super().__init__(application_id=APP_ID)
        self.side_effects = actions or SystemActions()
        self.win: SetupWindow | None = None

    def do_activate(self) -> None:
        if self.win is None:
            from hyprspot.ui import theme
            theme.load(CSS_PATH)
            self.win = SetupWindow(self, self.side_effects)
        self.win.present()


def run() -> int:
    return SetupApp().run([])
