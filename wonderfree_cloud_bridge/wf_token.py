# Auto-split from original bridge.py
# Module: wf_token
from __future__ import annotations
import time
import requests
from wf_config import APP_HEADERS, BASE_URL, LOGIN_PATH, SECRET_SUFFIX, log
from wf_crypto import _rand, make_pwd, make_signature

class TokenManager:
    def __init__(self, email: str, password: str, user_domain: str):
        self.email = email
        self.password = password
        self.user_domain = user_domain
        self.access_token = ""
        self.exp = 0  # epoch seconds when token expires

    def _api(self) -> requests.Session:
        s = requests.Session()
        for k, v in APP_HEADERS.items():
            s.headers[k] = v
        s.headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        return s

    def login(self) -> None:
        rnd = _rand(16)
        pwd_b64 = make_pwd(self.password, rnd)
        sig = make_signature(self.email, pwd_b64, rnd, SECRET_SUFFIX)

        data = {
            "email": self.email,
            "pwd": pwd_b64,
            "random": rnd,
            "userDomain": self.user_domain,
            "signature": sig,
        }

        r = self._api().post(BASE_URL + LOGIN_PATH, data=data, timeout=25)
        j = r.json()

        if r.status_code != 200 or j.get("code") != 200:
            raise RuntimeError(f"Login failed: {j}")

        tok = j.get("data") or {}
        at = tok.get("accessToken") or {}

        # token principale
        self.access_token = str(at.get("token") or tok.get("accessToken") or "")

        # scadenza token (gestiamo millisecondi o secondi)
        try:
            exp_val = float(at.get("expirationTime") or tok.get("expirationTime") or 0)
            if exp_val > 1e12:
                exp_val /= 1000.0
            self.exp = int(exp_val)
        except Exception:
            # fallback ~2h
            self.exp = int(time.time()) + 7200

        remaining = self.exp - int(time.time())
        log.info(f"Login OK; token acquired (valid for {remaining}s).")

    def ensure(self, skew: int = 600) -> str:
        """
        Torna un access token valido.
        Se mancano <=skew secondi alla scadenza, rifà login().
        Nessun refresh token: se serve ci si riloggia.
        """
        now = int(time.time())
        if self.access_token and now < self.exp - skew:
            return self.access_token

        # token mancante o quasi scaduto → login nuovo
        self.login()
        return self.access_token
