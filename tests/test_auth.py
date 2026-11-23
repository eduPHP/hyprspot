import base64
import hashlib
import json
import stat
import urllib.parse

from hyprspot.auth import REDIRECT_URI, SCOPES, TokenStore, authorize_url, pkce_pair


def test_pkce_challenge_matches_verifier():
    verifier, challenge = pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    assert 43 <= len(verifier) <= 128


def test_authorize_url_params():
    q = urllib.parse.parse_qs(urllib.parse.urlparse(authorize_url("cid", "chal", "st")).query)
    assert q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == [REDIRECT_URI]
    assert q["client_id"] == ["cid"]
    assert set(q["scope"][0].split()) == set(SCOPES.split())


def test_save_sets_0600_and_expiry(tmp_path):
    s = TokenStore(tmp_path / "token.json")
    s.save({"access_token": "a", "refresh_token": "r", "expires_in": 3600}, now=1000)
    assert stat.S_IMODE((tmp_path / "token.json").stat().st_mode) == 0o600
    assert s.load()["expires_at"] == 4600


def test_needs_refresh(tmp_path):
    s = TokenStore(tmp_path / "token.json")
    s.save({"access_token": "a", "refresh_token": "r", "expires_in": 3600}, now=1000)
    assert s.needs_refresh(4539) is False
    assert s.needs_refresh(4541) is True


def test_refresh_keeps_old_refresh_token(tmp_path):
    s = TokenStore(tmp_path / "token.json")
    s.save({"access_token": "a", "refresh_token": "r1", "expires_in": 3600, "client_id": "c"}, now=0)
    s.apply_refresh({"access_token": "b", "expires_in": 3600}, now=100)
    t = s.load()
    assert t["access_token"] == "b"
    assert t["refresh_token"] == "r1"
    assert t["client_id"] == "c"
    assert t["expires_at"] == 3700


def test_refresh_body(tmp_path):
    s = TokenStore(tmp_path / "token.json")
    s.save({"access_token": "a", "refresh_token": "r1", "expires_in": 1, "client_id": "c"}, now=0)
    assert s.refresh_body() == {"grant_type": "refresh_token", "refresh_token": "r1", "client_id": "c"}


def test_load_missing_returns_none(tmp_path):
    assert TokenStore(tmp_path / "nope.json").load() is None
    (tmp_path / "bad.json").write_text("{")
    assert TokenStore(tmp_path / "bad.json").load() is None


def test_expire_forces_refresh(tmp_path):
    s = TokenStore(tmp_path / "token.json")
    s.save({"access_token": "a", "refresh_token": "r", "expires_in": 3600}, now=1000)
    s.expire()
    assert s.needs_refresh(1001) is True
    assert s.load()["refresh_token"] == "r"


def test_has_scope(tmp_path):
    from hyprspot.auth import RECENT_SCOPE
    store = TokenStore(tmp_path / "t.json")
    store.save({"access_token": "a", "scope": "user-library-read playlist-read-private"})
    assert not store.has_scope(RECENT_SCOPE)
    store.save({"access_token": "a", "scope": f"user-library-read {RECENT_SCOPE}"})
    assert store.has_scope(RECENT_SCOPE)
    store.save({"access_token": "a"})          # token antigo sem o campo
    assert store.has_scope(RECENT_SCOPE)
