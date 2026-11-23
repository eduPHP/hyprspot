"""Web API do Spotify: playlists, busca e curtidas (assíncrono, libsoup3)."""
from __future__ import annotations

import json
import time
import urllib.parse
from typing import Callable

import gi

gi.require_version("Soup", "3.0")
from gi.repository import GLib, Soup  # noqa: E402

from hyprspot.auth import TOKEN_URL, TokenStore  # noqa: E402
from hyprspot.model import Playlist, Track  # noqa: E402

API = "https://api.spotify.com/v1"
SEARCH_LIMIT = 10
QUOTA_PAUSE_S = 60
ERR_OFFLINE = "Sem conexão com o Spotify"
ERR_QUOTA = "Limite da API atingido, tentando de novo em 1 min"
ERR_AUTH = "Login expirado: rode hyprspot login"
ERR_GENERIC = "Erro na API do Spotify"
ERR_NO_DEVICE = "O hyprspot não aparece como aparelho no Spotify"


# ---------------------------------------------------------------- funções puras
def _image(images: list | None, target: int = 300) -> str | None:
    imgs = [i for i in (images or []) if i.get("url")]
    if not imgs:
        return None
    return min(imgs, key=lambda i: abs((i.get("width") or target) - target))["url"]


def parse_playlists(body: dict) -> tuple[list[Playlist], str | None]:
    out = []
    for it in body.get("items") or []:
        if not it or not it.get("uri"):
            continue
        out.append(Playlist(uri=it["uri"], name=it.get("name") or "",
                            owner=(it.get("owner") or {}).get("display_name") or "",
                            image_url=_image(it.get("images"))))
    return out, body.get("next")


def _track(t: dict) -> Track:
    return Track(uri=t["uri"], title=t.get("name") or "",
                 artist=", ".join(a.get("name", "") for a in t.get("artists") or [] if a.get("name")),
                 album=(t.get("album") or {}).get("name") or "", duration_ms=int(t.get("duration_ms") or 0),
                 art_url=_image((t.get("album") or {}).get("images")))


def parse_track(body: dict) -> Track | None:
    return _track(body) if body and body.get("uri") else None


def fill_track(track: Track | None, known: dict[str, Track]) -> Track | None:
    """O Soloist às vezes manda a faixa sem nome/capa (ex.: autoplay); completa com o que a Web API trouxe."""
    if track is None or track.title:
        return track
    return known.get(track.uri, track)


def parse_search(body: dict) -> list[Track]:
    return [_track(t) for t in ((body.get("tracks") or {}).get("items") or []) if t and t.get("uri")]


def parse_search_playlists(body: dict) -> list[Playlist]:
    return parse_playlists((body or {}).get("playlists") or {})[0]


def parse_recent_playlists(body: dict) -> list[str]:
    """URIs das playlists em "tocadas recentemente", da mais recente para a mais antiga, sem repetir."""
    out: list[str] = []
    for it in (body or {}).get("items") or []:
        ctx = (it or {}).get("context") or {}
        uri = ctx.get("uri") or ""
        if ctx.get("type") == "playlist" and uri.startswith("spotify:playlist:") and uri not in out:
            out.append(uri)
    return out


def parse_playlist(body: dict) -> Playlist | None:
    return parse_playlists({"items": [body]})[0][0] if body and body.get("uri") else None


def liked_playlist(user_id: str) -> Playlist:
    """'Músicas Curtidas' como contexto de verdade (o mesmo URI que os apps oficiais usam)."""
    return Playlist(f"spotify:user:{user_id}:collection", "Músicas Curtidas", "Sua biblioteca", None)


def search_url(q: str, offset: int) -> str:
    return API + "/search?" + urllib.parse.urlencode(
        {"q": q, "type": "track,playlist", "limit": SEARCH_LIMIT, "offset": offset})


def retry_delay(status: int, headers: dict, body: dict | None) -> float | None:
    if status == 429:
        reason = ((body or {}).get("error") or {}).get("reason")
        if reason == "QUOTA_EXCEEDED":
            return QUOTA_PAUSE_S
        try:
            return float(headers.get("Retry-After", 1))
        except (TypeError, ValueError):
            return 1.0
    if 500 <= status < 600:
        return 2.0
    return None


# ---------------------------------------------------------------- cliente
# Transporte: (method, url, headers, body_bytes|None, cb(status, headers, raw_bytes, error|None))
Transport = Callable[[str, str, dict, bytes | None, Callable[[int, dict, bytes, str | None], None]], None]


def soup_transport(session: Soup.Session) -> Transport:
    def send(method, url, headers, body, cb):
        msg = Soup.Message.new(method, url)
        for k, v in headers.items():
            msg.get_request_headers().append(k, v)
        if body is not None:
            msg.set_request_body_from_bytes(headers.get("Content-Type", "application/json"), GLib.Bytes.new(body))

        def done(sess, res):
            try:
                raw = sess.send_and_read_finish(res).get_data() or b""
            except GLib.Error as e:
                cb(0, {}, b"", e.message or "network")
                return
            hdrs = {"Retry-After": msg.get_response_headers().get_one("Retry-After")}
            cb(msg.get_status(), hdrs, raw, None)

        session.send_and_read_async(msg, GLib.PRIORITY_DEFAULT, None, done)

    return send


def _json(raw: bytes):
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


class WebApi:
    def __init__(self, store: TokenStore, session: Soup.Session | None = None):
        self.store = store
        self._transport: Transport = soup_transport(session or Soup.Session())
        self._blocked_until = 0.0
        self._refreshing: list[Callable[[str | None], None]] = []
        self._device_id: str | None = None

    # -- token: cb(None) = ok, cb(ERR_*) = falha --
    def _ensure_token(self, cb: Callable[[str | None], None]) -> None:
        if self.store.load() is None:
            cb(ERR_AUTH)
            return
        if not self.store.needs_refresh():
            cb(None)
            return
        self._refreshing.append(cb)
        if len(self._refreshing) > 1:
            return
        body = urllib.parse.urlencode(self.store.refresh_body()).encode()

        def done(status, _headers, raw, error):
            result: str | None = ERR_GENERIC
            try:
                if error:
                    result = ERR_OFFLINE
                elif status == 200 and isinstance(_json(raw), dict):
                    self.store.apply_refresh(_json(raw))
                    result = None
                elif status in (400, 401):
                    result = ERR_AUTH
            except Exception:  # noqa: BLE001 - nunca deixar os waiters presos
                result = ERR_AUTH
            finally:
                waiters, self._refreshing = self._refreshing, []
                for w in waiters:
                    w(result)

        self._transport("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body, done)

    # -- requisição genérica: cb(status, data, error) --
    def _request(self, method: str, url: str, cb: Callable[[int, object, str | None], None],
                 body: dict | None = None, retries: int = 2) -> None:
        if time.monotonic() < self._blocked_until:
            cb(0, None, ERR_QUOTA)
            return

        def with_token(err: str | None):
            if err:
                cb(0, None, err)
                return
            headers = {"Authorization": f"Bearer {self.store.access_token()}", "Content-Type": "application/json"}
            payload = json.dumps(body).encode() if body is not None else None

            def done(status, resp_headers, raw, error):
                if error:
                    cb(0, None, ERR_OFFLINE)
                    return
                data = _json(raw)
                if status == 401 and retries > 0:
                    self.store.expire()
                    self._request(method, url, cb, body, retries - 1)
                    return
                delay = retry_delay(status, resp_headers, data if isinstance(data, dict) else None)
                if delay is not None:
                    if delay >= QUOTA_PAUSE_S:
                        self._blocked_until = time.monotonic() + delay
                        cb(status, None, ERR_QUOTA)
                        return
                    if retries > 0:
                        GLib.timeout_add(int(delay * 1000),
                                         lambda: (self._request(method, url, cb, body, retries - 1), False)[1])
                        return
                if 200 <= status < 300:
                    cb(status, data if isinstance(data, dict | list) else None, None)
                else:
                    cb(status, None, ERR_GENERIC)

            self._transport(method, url if url.startswith("http") else API + url, headers, payload, done)

        self._ensure_token(with_token)

    # -- endpoints --
    def list_playlists(self, cb: Callable[[list[Playlist] | None, str | None], None]) -> None:
        acc: list[Playlist] = []

        def page(_status, data, err):
            if err:
                cb(None, err)
                return
            items, nxt = parse_playlists(data or {})
            acc.extend(items)
            if nxt:
                self._request("GET", nxt, page)
            else:
                cb(acc, None)

        self._request("GET", "/me/playlists?limit=50", page)

    def search(self, q: str, offset: int, cb: Callable[[list[Track] | None, str | None], None],
               on_playlists: Callable[[list[Playlist]], None] | None = None) -> None:
        """Músicas em `cb`; as playlists da mesma resposta (se pedidas) vão antes, em `on_playlists`."""
        def done(_s, d, e):
            if e:
                cb(None, e)
                return
            if on_playlists is not None:
                on_playlists(parse_search_playlists(d or {}))
            cb(parse_search(d or {}), None)

        self._request("GET", search_url(q, offset), done)

    def track(self, uri: str, cb: Callable[[Track | None], None]) -> None:
        tid = uri.rsplit(":", 1)[-1]
        self._request("GET", f"/tracks/{tid}", lambda _s, d, e: cb(None if e else parse_track(d or {})))

    def recent_playlists(self, cb: Callable[[list[str] | None], None]) -> None:
        """Playlists tocadas em qualquer aparelho (o Spotify guarda só as últimas 50 músicas)."""
        self._request("GET", "/me/player/recently-played?limit=50",
                      lambda _s, d, e: cb(None if e else parse_recent_playlists(d or {})))

    def playlist(self, uri: str, cb: Callable[[Playlist | None], None]) -> None:
        pid = uri.rsplit(":", 1)[-1]
        self._request("GET", f"/playlists/{pid}?fields=uri,name,owner(display_name),images",
                      lambda _s, d, e: cb(None if e else parse_playlist(d or {})))

    def user_id(self, cb: Callable[[str | None], None]) -> None:
        self._request("GET", "/me", lambda _s, d, e: cb(None if e else (d or {}).get("id")))

    def liked_count(self, cb: Callable[[int | None], None]) -> None:
        self._request("GET", "/me/tracks?limit=1", lambda _s, d, e: cb(None if e else int((d or {}).get("total") or 0)))

    def is_liked(self, uri: str, cb: Callable[[bool | None], None]) -> None:
        self._request("GET", "/me/library/contains?" + urllib.parse.urlencode({"uris": uri}),
                      lambda _s, d, e: cb(None if e or not isinstance(d, list) or not d else bool(d[0])))

    def play_at(self, device_name: str, context_uri: str, track_uri: str,
                cb: Callable[[str | None], None]) -> None:
        """Toca `context_uri` a partir de `track_uri` no aparelho do hyprspot (o Soloist não sabe fazer isso)."""
        body = {"context_uri": context_uri, "offset": {"uri": track_uri}}

        def play(device_id: str, retry: bool):
            def done(status, _d, err):
                if status == 404 and retry:  # o id muda quando o Soloist reinicia
                    self._device_id = None
                    with_device(False)
                    return
                cb(err)
            self._request("PUT", f"/me/player/play?device_id={urllib.parse.quote(device_id)}", done, body=body)

        def with_device(retry: bool):
            if self._device_id:
                play(self._device_id, retry)
                return

            def got(_s, d, err):
                if err:
                    cb(err)
                    return
                dev = next((x for x in (d or {}).get("devices", []) if x.get("name") == device_name), None)
                if not dev or not dev.get("id"):
                    cb(ERR_NO_DEVICE)
                    return
                self._device_id = dev["id"]
                play(self._device_id, retry)

            self._request("GET", "/me/player/devices", got)

        with_device(True)

    def set_liked(self, uri: str, liked: bool, cb: Callable[[bool], None]) -> None:
        # o URI vai no endereço: no corpo o Spotify responde 400 "Missing required field: uris"
        self._request("PUT" if liked else "DELETE", "/me/library?" + urllib.parse.urlencode({"uris": uri}),
                      lambda _s, _d, e: cb(e is None))
