"""Login PKCE na Web API do Spotify e armazenamento do token (0600)."""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

REDIRECT_URI = "http://127.0.0.1:8898/callback"
SCOPES = " ".join([
    "user-read-playback-state", "user-modify-playback-state", "user-read-currently-playing",
    "playlist-read-private", "playlist-read-collaborative", "user-library-read", "user-library-modify",
    "user-read-recently-played",
])
RECENT_SCOPE = "user-read-recently-played"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
REFRESH_MARGIN_S = 60


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(client_id: str, challenge: str, state: str) -> str:
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT_URI, "scope": SCOPES,
        "code_challenge_method": "S256", "code_challenge": challenge, "state": state,
    })


class TokenStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict | None:
        try:
            return json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def save(self, tok: dict, now: float | None = None) -> None:
        now = time.time() if now is None else now
        tok = dict(tok)
        tok["expires_at"] = now + tok.get("expires_in", 3600)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(tok, f)
        os.chmod(self.path, 0o600)

    def needs_refresh(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        tok = self.load()
        return tok is None or now >= tok.get("expires_at", 0) - REFRESH_MARGIN_S

    def refresh_body(self) -> dict:
        tok = self.load() or {}
        return {"grant_type": "refresh_token", "refresh_token": tok.get("refresh_token", ""),
                "client_id": tok.get("client_id", "")}

    def apply_refresh(self, resp: dict, now: float | None = None) -> None:
        tok = self.load() or {}
        tok.update(resp)
        self.save(tok, now)

    def expire(self) -> None:
        """Força o refresh na próxima requisição (ex.: depois de um 401)."""
        tok = self.load()
        if tok:
            tok["expires_at"] = 0
            self.path.write_text(json.dumps(tok))
            os.chmod(self.path, 0o600)

    def has_scope(self, scope: str) -> bool:
        """O login atual deu essa permissão? (token antigo sem `scope` conta como sim)."""
        tok = self.load() or {}
        granted = tok.get("scope")
        return granted is None or scope in granted.split()

    def access_token(self) -> str | None:
        tok = self.load()
        return tok.get("access_token") if tok else None


def run_login(client_id: str, store: TokenStore) -> None:
    """Login interativo (bloqueante): abre o navegador e espera o callback em 127.0.0.1:8898."""
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    url = authorize_url(client_id, challenge, state)
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<p>hyprspot: login concluído, pode fechar esta aba.</p>".encode())

        def log_message(self, *_a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 8898), Handler)
    print("Abrindo o navegador para autorizar o hyprspot…\n" + url, flush=True)
    webbrowser.open(url)
    while "code" not in result and "error" not in result:
        srv.handle_request()
    srv.server_close()
    if result.get("state") != state or "error" in result:
        raise SystemExit(f"Login falhou: {result.get('error', 'state inválido')}")
    body = urllib.parse.urlencode({"grant_type": "authorization_code", "code": result["code"],
                                   "redirect_uri": REDIRECT_URI, "client_id": client_id,
                                   "code_verifier": verifier}).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req) as r:
        tok = json.load(r)
    tok["client_id"] = client_id
    store.save(tok)
    print(f"Token salvo em {store.path}")
