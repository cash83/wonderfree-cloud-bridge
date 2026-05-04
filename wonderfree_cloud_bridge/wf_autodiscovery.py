"""wf_autodiscovery.py - Auto-discovery generica per bridge Acceleronix

Con il campo 'platform' nel config, tutti i parametri tecnici
(base_url, accel_url, wf_domain, secret_suffix, realtime_attrs_url)
vengono impostati automaticamente.

Config minimo:
    wf_email, wf_password, platform, mqtt_host, mqtt_user, mqtt_pass

Parametri auto-scoperti (via API dopo login):
    device_key, product_key, accel_client

Fonte dati piattaforma: app_dokit_env.yml estratto dall'APK Quectel.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import random
import string
import time
from typing import Any, Dict, List, Optional

import requests
from Crypto.Cipher import AES
from wf_privacy import mask_value

log = logging.getLogger("wf-autodiscovery")

_CACHE_PATH = "/data/discovered.json"
_CACHE_TTL  = 86400 * 30   # 30 giorni

# ── Catalogo piattaforme (da app_dokit_env.yml nell'APK) ─────────────────────
# Chiave: nome piattaforma (valore del campo 'platform' nel config)
# userDomainSecret = secret_suffix usato per firmare il login
_PLATFORMS: Dict[str, Dict[str, str]] = {

    # Wonderfree — Europe/Acceleronix (acceleronix.io)
    "wonderfree": {
        "base_url":           "https://iot-api.acceleronix.io",
        "accel_url":          "wss://iot-south.acceleronix.io:8443/ws/v2",
        "wf_domain":          "E.SP.4294967410",
        "secret_suffix":      "3aRNUwWahjyANa7WfBK2wCCkxCexB6nXxKJwXxfePvzf",
        "realtime_attrs_url": "https://iot-api.acceleronix.io/v2/binding/enduserapi/getDeviceBusinessAttributes",
    },

    # Landbook — North America/Netprisma (netprisma.us)
    "landbook": {
        "base_url":           "https://iot-api.netprisma.us",
        "accel_url":          "wss://iot-south.netprisma.us:8443/ws/v2",
        "wf_domain":          "U.SP.8589934603",
        "secret_suffix":      "pUTp5goB1bLinprRQMmK3EPiiuPiGrJtKUNptWRXVmP",
        "realtime_attrs_url": "https://iot-api.netprisma.us/v2/binding/enduserapi/getDeviceBusinessAttributes",
    },

    # Landecia — white-label Netprisma su dominio landecia.com
    "landecia": {
        "base_url":           "https://iot-api.landecia.com",
        "accel_url":          "wss://iot-south.landecia.com:8443/ws/v2",
        "wf_domain":          "U.SP.8589934603",
        "secret_suffix":      "pUTp5goB1bLinprRQMmK3EPiiuPiGrJtKUNptWRXVmP",
        "realtime_attrs_url": "https://iot-api.netprisma.us/v2/binding/enduserapi/getDeviceBusinessAttributes",
    },

    # North America — alias diretto Netprisma
    "northamerica": {
        "base_url":           "https://iot-api.netprisma.us",
        "accel_url":          "wss://iot-south.netprisma.us:8443/ws/v2",
        "wf_domain":          "U.SP.8589934603",
        "secret_suffix":      "pUTp5goB1bLinprRQMmK3EPiiuPiGrJtKUNptWRXVmP",
        "realtime_attrs_url": "https://iot-api.netprisma.us/v2/binding/enduserapi/getDeviceBusinessAttributes",
    },

    # Europe — alias diretto Acceleronix
    "europe": {
        "base_url":           "https://iot-api.acceleronix.io",
        "accel_url":          "wss://iot-south.acceleronix.io:8443/ws/v2",
        "wf_domain":          "E.SP.4294967410",
        "secret_suffix":      "3aRNUwWahjyANa7WfBK2wCCkxCexB6nXxKJwXxfePvzf",
        "realtime_attrs_url": "https://iot-api.acceleronix.io/v2/binding/enduserapi/getDeviceBusinessAttributes",
    },

    # China — Quectel CN
    "china": {
        "base_url":           "https://iot-api.quectelcn.com",
        "accel_url":          "wss://iot-south.quectelcn.com:8443/ws/v2",
        "wf_domain":          "C.DM.5903.1",
        "secret_suffix":      "EufftRJSuWuVY7c6txzGifV9bJcfXHAFa7hXY5doXSn7",
        "realtime_attrs_url": "https://iot-api.quectelcn.com/v2/binding/enduserapi/getDeviceBusinessAttributes",
    },

    # Europe UAT (test/staging Acceleronix)
    "europe-uat": {
        "base_url":           "https://uat-iot-api.acceleronix.io",
        "accel_url":          "wss://uat-iot-south.acceleronix.io:8443/ws/v2",
        "wf_domain":          "E.SP.4294967410",
        "secret_suffix":      "4oARJu9W1axes3atB37zmnPLVFpgqHs7YEMS622XXFvs",
        "realtime_attrs_url": "https://uat-iot-api.acceleronix.io/v2/binding/enduserapi/getDeviceBusinessAttributes",
    },

    # North America UAT
    "northamerica-uat": {
        "base_url":           "https://uat-iot-api.quectelus.com",
        "accel_url":          "wss://uat-iot-south.quectelus.com:8443/ws/v2",
        "wf_domain":          "U.SP.8589934603",
        "secret_suffix":      "AKME5rKVH1eSevuGCHitNJ6tY28yGiGw6qHuVMQ7YyGQ",
        "realtime_attrs_url": "https://uat-iot-api.quectelus.com/v2/binding/enduserapi/getDeviceBusinessAttributes",
    },

    # China UAT
    "china-uat": {
        "base_url":           "https://uat-iot-api.quectelcn.com",
        "accel_url":          "wss://uat-iot-south.quectelcn.com:8443/ws/v2",
        "wf_domain":          "C.DM.5903.1",
        "secret_suffix":      "EufftRJSuWuVY7c6txzGifV9bJcfXHAFa7hXY5doXSn7",
        "realtime_attrs_url": "https://uat-iot-api.quectelcn.com/v2/binding/enduserapi/getDeviceBusinessAttributes",
    },
}

# ── Crypto inline (no import da wf_config/wf_crypto) ─────────────────────────

def _load_platforms_from_file(defaults: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    path = os.path.join(os.path.dirname(__file__), "platforms.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        if not isinstance(data, dict):
            raise ValueError("platforms.json non contiene un oggetto JSON")
        required = {"base_url", "accel_url", "wf_domain", "secret_suffix", "realtime_attrs_url"}
        for name, cfg in data.items():
            if not isinstance(cfg, dict):
                raise ValueError(f"config piattaforma non valida: {name}")
            missing = sorted(required - set(cfg))
            if missing:
                raise ValueError(f"config piattaforma incompleta: {name} ({', '.join(missing)})")
        return {str(k): {str(ck): str(cv) for ck, cv in v.items()} for k, v in data.items()}
    except Exception as e:
        log.warning(f"[AUTODISCOVERY] platforms.json non caricato ({e}); uso fallback interno")
        return defaults


_PLATFORMS = _load_platforms_from_file(_PLATFORMS)


def _rand(n: int = 16) -> str:
    a = string.ascii_letters + string.digits
    return "".join(random.choice(a) for _ in range(n))


def _pkcs7_pad(b: bytes, block: int = 16) -> bytes:
    pad = block - (len(b) % block)
    return b + bytes([pad]) * pad


def _make_pwd(password: str, rnd: str) -> str:
    md5hex = hashlib.md5(rnd.encode()).hexdigest()
    mid    = md5hex[8:24]
    key    = mid.upper().encode("ascii")
    iv     = (mid[8:16] + mid[0:8]).upper().encode("ascii")
    ct     = AES.new(key, AES.MODE_CBC, iv).encrypt(
                 _pkcs7_pad(password.encode(), 16))
    return base64.b64encode(ct).decode("ascii")


def _make_sig(email: str, pwd_b64: str, rnd: str, secret_suffix: str) -> str:
    s = f"{email}{pwd_b64}{rnd}{secret_suffix}"
    return hashlib.sha256(s.encode()).hexdigest()


def _normalize_auth(token: str) -> str:
    tok = token.strip()
    return tok if tok.lower().startswith("bearer ") else f"Bearer {tok}"

# ── Lettura config ────────────────────────────────────────────────────────────

def _read_options() -> dict:
    try:
        p = "/data/options.json"
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception:
        pass
    return {}


def _get(env_key: str, opts: dict, opt_key: str = "") -> str:
    v = os.getenv(env_key, "").strip()
    if v:
        return v
    k = opt_key or env_key.lower()
    return str(opts.get(k, "")).strip()

# ── Cache ─────────────────────────────────────────────────────────────────────

def _load_cache(current_platform: str = "") -> Optional[dict]:
    try:
        if not os.path.exists(_CACHE_PATH):
            return None
        with open(_CACHE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if int(time.time()) - int(d.get("_ts", 0)) > _CACHE_TTL:
            log.info("[AUTODISCOVERY] Cache scaduta, rifaccio discovery")
            return None
        cached_platform = d.get("_platform", "")
        if current_platform and cached_platform and cached_platform != current_platform:
            log.warning(
                f"[AUTODISCOVERY] Piattaforma cambiata ({cached_platform} → {current_platform}), "
                "cache invalidata — riavvio discovery."
            )
            return None
        # Valida che il prefisso dell'accel_client corrisponda al dominio della piattaforma.
        # Esempio: piattaforma "wonderfree" (dominio E.SP...) richiede qu_E..., non qu_U... o qu_UE...
        if current_platform:
            plat_cfg = _PLATFORMS.get(current_platform, {})
            plat_domain = plat_cfg.get("wf_domain", "")
            if plat_domain and plat_domain[0].isalpha():
                expected_prefix = plat_domain[0].upper()
                cached_accel = d.get("accel_client", "")
                # Estrae il prefisso del client ID: "qu_E82519_" → 'E', "qu_UE82519_" → 'U'
                if cached_accel.startswith("qu_") and len(cached_accel) > 3 and cached_accel[3].isalpha():
                    actual_prefix = cached_accel[3]
                    if actual_prefix != expected_prefix:
                        log.warning(
                            f"[AUTODISCOVERY] Cache accel_client prefix '{actual_prefix}' != "
                            f"atteso '{expected_prefix}' (dominio {plat_domain}) — cache invalidata."
                        )
                        return None
        log.info("[AUTODISCOVERY] Valori caricati dalla cache")
        return d
    except Exception:
        return None


def _save_cache(data: dict, platform: str = "") -> None:
    try:
        data["_ts"] = int(time.time())
        if platform:
            data["_platform"] = platform
        dirn = os.path.dirname(_CACHE_PATH)
        if dirn:
            os.makedirs(dirn, exist_ok=True)
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        log.info(f"[AUTODISCOVERY] Cache salvata in {_CACHE_PATH}")
    except Exception as e:
        log.warning(f"[AUTODISCOVERY] Impossibile salvare cache: {e}")

# ── Login ─────────────────────────────────────────────────────────────────────

def _build_headers(app_id: str = "584", app_version: str = "3.3.1") -> Dict[str, str]:
    return {
        "appVersion":    app_version,
        "appSystemType": "android",
        "appId":         app_id,
        "Accept":        "application/json",
        "Content-Type":  "application/x-www-form-urlencoded; charset=UTF-8",
    }


def _login(base_url: str, login_path: str, secret_suffix: str,
           email: str, password: str, domain: str,
           headers: dict) -> Optional[dict]:
    rnd     = _rand(16)
    pwd_b64 = _make_pwd(password, rnd)
    sig     = _make_sig(email, pwd_b64, rnd, secret_suffix)
    data    = {
        "email": email, "pwd": pwd_b64,
        "random": rnd, "userDomain": domain, "signature": sig,
    }
    try:
        r = requests.post(
            base_url.rstrip("/") + login_path,
            data=data, headers=headers, timeout=15,
        )
        j = r.json()
        if j.get("code") == 200:
            return j
        log.warning(
            f"[AUTODISCOVERY] Login rifiutato dal server: code={j.get('code')} msg={j.get('msg', '')} "
            f"(url={base_url}, domain={domain})"
        )
    except Exception as e:
        log.warning(f"[AUTODISCOVERY] Login exception: {e}")
    return None


def _extract_token(resp: dict) -> str:
    d  = resp.get("data") or {}
    at = d.get("accessToken") or {}
    if isinstance(at, dict):
        return str(at.get("token") or "")
    return str(at or d.get("token") or "")


def _decode_jwt_uid(token_str: str) -> Optional[str]:
    try:
        tok = token_str.strip()
        if tok.lower().startswith("bearer "):
            tok = tok[7:].strip()
        parts = tok.split(".")
        if len(parts) < 2:
            return None
        p = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(p).decode())
        uid = payload.get("uid") or payload.get("userId")
        if uid and str(uid) != "subject":
            return str(uid)
    except Exception:
        pass
    return None


def _extract_user_id(resp: dict) -> Optional[str]:
    d = resp.get("data") or {}
    for tok_field in ("accessToken", "refreshToken"):
        tok_obj = d.get(tok_field) or {}
        tok_str = tok_obj.get("token") if isinstance(tok_obj, dict) else str(tok_obj or "")
        if tok_str:
            uid = _decode_jwt_uid(tok_str)
            if uid:
                return uid
    for key in ("userId", "uid", "id", "user_id"):
        v = d.get(key)
        if v:
            return str(v)
    return None


def _get_user_info(base_url: str, token: str, headers: dict) -> Optional[dict]:
    auth_h = {**headers, "Authorization": _normalize_auth(token)}
    try:
        r = requests.get(
            base_url.rstrip("/") + "/v2/enduser/enduserapi/userInfo",
            headers=auth_h, timeout=8,
        )
        j = r.json()
        if j.get("code") == 200:
            return j.get("data") or {}
    except Exception:
        pass
    return None

# ── Device list ───────────────────────────────────────────────────────────────

_BINDING_PATHS = [
    "/v2/binding/enduserapi/userDeviceList",
    "/v2/binding/enduserapi/getBindingList",
    "/v2/binding/enduserapi/getUserBindingList",
    "/v2/binding/enduserapi/getDeviceList",
]

_BINDING_PARAMS = [
    {"pageSize": 20, "isAssociated": 1},
    {"pageSize": 20, "isAssociated": "true"},
    {"pageSize": 20},
    {},
]


def _get_devices(base_url: str, token: str, headers: dict) -> List[dict]:
    auth_h = {**headers, "Authorization": _normalize_auth(token)}
    for path in _BINDING_PATHS:
        for params in _BINDING_PARAMS:
            try:
                r = requests.get(
                    base_url.rstrip("/") + path,
                    params=params, headers=auth_h, timeout=10,
                )
                j = r.json()
                log.debug(
                    f"[AUTODISCOVERY] {path} params={params} "
                    f"→ code={j.get('code')} data={type(j.get('data')).__name__}"
                )
                if j.get("code") == 200:
                    items: Any = j.get("data") or []
                    if isinstance(items, dict):
                        items = (
                            items.get("list")
                            or items.get("records")
                            or items.get("data")
                            or list(items.values())
                        )
                    if isinstance(items, list) and items:
                        log.info(f"[AUTODISCOVERY] {len(items)} dispositivi trovati → {path}")
                        return items
            except Exception as e:
                log.debug(f"[AUTODISCOVERY] {path} errore: {e}")
    return []


def _parse_device(dev: dict) -> tuple[str, str]:
    dk = str(dev.get("deviceKey") or dev.get("dk") or dev.get("device_key") or "")
    pk = str(dev.get("productKey") or dev.get("pk") or dev.get("product_key") or "")
    return dk, pk

# ── Applica valori a os.environ ───────────────────────────────────────────────

def _setenv(key: str, val: str) -> None:
    """Imposta env var solo se non già presente."""
    if val and not os.getenv(key, "").strip():
        os.environ[key] = val


def _apply_platform(plat: dict) -> None:
    _setenv("BASE_URL",           plat.get("base_url", ""))
    _setenv("ACCEL_URL",          plat.get("accel_url", ""))
    _setenv("WF_DOMAIN",          plat.get("wf_domain", ""))
    _setenv("SECRET_SUFFIX",      plat.get("secret_suffix", ""))
    _setenv("REALTIME_ATTRS_URL", plat.get("realtime_attrs_url", ""))


def _apply_discovered(data: dict) -> None:
    for k, env_k in {
        "wf_domain":    "WF_DOMAIN",
        "device_key":   "DEVICE_KEY",
        "product_key":  "PRODUCT_KEY",
        "accel_client": "ACCEL_CLIENT",
    }.items():
        v = data.get(k, "")
        if v:
            _setenv(env_k, v)
            if os.getenv(env_k) == v:
                log.info(f"[AUTODISCOVERY] {env_k} = {mask_value(v)}")

# ── Entry point ───────────────────────────────────────────────────────────────

def setup(force: bool = False) -> None:
    """
    Chiamare da bridge.py PRIMA di importare wf_config.

    Con 'platform' nel config, imposta automaticamente:
      base_url, accel_url, wf_domain, secret_suffix, realtime_attrs_url

    Poi scopre automaticamente:
      device_key, product_key, accel_client

    Config minimo richiesto:
      wf_email, wf_password, platform (o tutti i parametri manuali)
    """
    opts = _read_options()

    # ── Credenziali ───────────────────────────────────────────────────────
    email    = _get("WF_EMAIL",    opts, "wf_email").lower()
    password = _get("WF_PASSWORD", opts, "wf_password")

    if not email or not password:
        raise SystemExit("[AUTODISCOVERY] wf_email e wf_password sono obbligatori.")

    # ── Selezione piattaforma ─────────────────────────────────────────────
    platform = _get("PLATFORM", opts, "app").lower().strip()

    if platform and platform != "custom":
        plat_cfg = _PLATFORMS.get(platform)
        if plat_cfg is None:
            available = ", ".join(sorted(_PLATFORMS.keys()))
            raise SystemExit(
                f"[AUTODISCOVERY] App '{platform}' non riconosciuta.\n"
                f"Valori disponibili: {available}\n"
                f"Oppure usa 'custom' e inserisci i parametri manualmente."
            )
        log.info(f"[AUTODISCOVERY] App: {platform} → {plat_cfg['base_url']}")
        _apply_platform(plat_cfg)
    else:
        # Modalità custom: i parametri devono essere tutti nel config
        if not _get("BASE_URL",      opts, "base_url"):
            raise SystemExit("[AUTODISCOVERY] 'base_url' obbligatorio in modalità custom.")
        if not _get("SECRET_SUFFIX", opts, "secret_suffix"):
            raise SystemExit("[AUTODISCOVERY] 'secret_suffix' obbligatorio in modalità custom.")
        if not _get("ACCEL_URL",     opts, "accel_url"):
            raise SystemExit("[AUTODISCOVERY] 'accel_url' obbligatorio in modalità custom.")
        if not _get("REALTIME_ATTRS_URL", opts, "realtime_attrs_url"):
            raise SystemExit("[AUTODISCOVERY] 'realtime_attrs_url' obbligatorio in modalità custom.")
        if not _get("WF_DOMAIN",     opts, "wf_domain"):
            raise SystemExit("[AUTODISCOVERY] 'wf_domain' obbligatorio in modalità custom.")
        log.info("[AUTODISCOVERY] Modalità custom — uso parametri dal config.")

    # Leggi i valori ora (dopo aver applicato la piattaforma)
    base_url      = os.getenv("BASE_URL", "").strip() or _get("BASE_URL", opts, "base_url")
    secret_suffix = os.getenv("SECRET_SUFFIX", "").strip() or _get("SECRET_SUFFIX", opts, "secret_suffix")
    login_path    = _get("LOGIN_PATH", opts, "login_path") or "/v2/enduser/enduserapi/emailPwdLogin"
    app_id        = _get("APP_ID",     opts, "app_id")     or "584"
    app_version   = _get("APP_VERSION", opts, "app_version") or "3.3.1"
    headers       = _build_headers(app_id, app_version)

    # ── Verifica se config già completo ───────────────────────────────────
    domain      = _get("WF_DOMAIN",    opts, "wf_domain")  or os.getenv("WF_DOMAIN", "")
    device_key  = _get("DEVICE_KEY",   opts, "device_key")
    product_key = _get("PRODUCT_KEY",  opts, "product_key")
    accel_cli   = _get("ACCEL_CLIENT", opts, "accel_client")
    preferred_device_key = (
        _get("WF_DEVICE_KEY", opts, "wf_device_key")
        or _get("DEVICE_KEY", opts, "device_key")
    )

    if domain and device_key and product_key and accel_cli:
        log.info("[AUTODISCOVERY] Config completo — skip discovery.")
        return

    # ── Carica dalla cache ────────────────────────────────────────────────
    if not force:
        cached = _load_cache(platform)
        if cached:
            _apply_discovered(cached)
            return

    log.info("[AUTODISCOVERY] *** Avvio auto-discovery ***")

    domain = domain or os.getenv("WF_DOMAIN", "")
    if not domain:
        raise SystemExit(
            "[AUTODISCOVERY] wf_domain non trovato.\n"
            "Imposta 'app' nel config oppure specifica 'wf_domain' manualmente."
        )

    # ── Login ─────────────────────────────────────────────────────────────
    log.info(f"[AUTODISCOVERY] Login → {base_url}  domain={domain}")
    resp = _login(base_url, login_path, secret_suffix, email, password, domain, headers)
    if not resp:
        hint = ""
        if platform in ("wonderfree", "europe"):
            hint = (
                "\n  → Stai usando la piattaforma EUROPEA (acceleronix.io)."
                "\n  → Se il tuo dispositivo è Landecia/Landbook usa 'landecia' o 'landbook' nel campo 'app'."
            )
        elif platform in ("landbook", "landecia", "northamerica"):
            hint = (
                "\n  → Stai usando la piattaforma NORD AMERICA (netprisma.us/landecia.com)."
                "\n  → Se il tuo dispositivo è Wonderfree Europe usa 'wonderfree' o 'europe' nel campo 'app'."
            )
        raise SystemExit(
            f"[AUTODISCOVERY] Login fallito su {base_url} (domain={domain}).{hint}\n"
            "Controlla: wf_email, wf_password, app (o base_url/secret_suffix/wf_domain)"
        )

    token   = _extract_token(resp)
    user_id = _extract_user_id(resp)

    # ── userInfo post-login ───────────────────────────────────────────────
    log.info("[AUTODISCOVERY] Chiamo /userInfo...")
    user_info = _get_user_info(base_url, token, headers)
    if user_info:
        uid = user_info.get("uid") or user_info.get("userId") or user_info.get("id")
        if uid:
            user_id = str(uid)
            log.info(f"[AUTODISCOVERY] uid = {user_id}")

    # ── accel_client ──────────────────────────────────────────────────────
    if not accel_cli:
        if user_id:
            uid_str = str(user_id)
            # Il prefisso dell'accel_client segue il prefisso del wf_domain:
            #   E.SP.4294967410 → E  (Europe/Acceleronix)
            #   U.SP.8589934603 → U  (North America/Netprisma)
            #   C.DM.5903.1     → C  (China/Quectel)
            # Se l'ID ha già un prefisso letterale, lo preserva.
            # Se è numerico, aggiunge il prefisso derivato dal dominio.
            if uid_str and uid_str[0].isdigit():
                domain_prefix = domain[0].upper() if domain and domain[0].isalpha() else "U"
                prefix = f"{domain_prefix}{uid_str}"
            else:
                prefix = uid_str
            accel_cli = f"qu_{prefix}_"
            log.info(f"[AUTODISCOVERY] accel_client = {mask_value(accel_cli)}  (domain_prefix={domain[0] if domain else '?'})")
        else:
            raise SystemExit(
                "[AUTODISCOVERY] userId non trovato.\n"
                "Aggiungi 'accel_client' nel config (es: qu_U24701_)."
            )

    # ── Device list ───────────────────────────────────────────────────────
    if not device_key or not product_key:
        log.info("[AUTODISCOVERY] Cerco dispositivi...")
        devices = _get_devices(base_url, token, headers)

        if not devices:
            raise SystemExit(
                "[AUTODISCOVERY] Nessun dispositivo trovato.\n"
                "Aggiungi device_key e product_key nel config manualmente."
            )

        if len(devices) > 1:
            log.info(f"[AUTODISCOVERY] {len(devices)} dispositivi trovati:")
            for i, d in enumerate(devices):
                dk, pk = _parse_device(d)
                log.info(f"  [{i}] dk={mask_value(dk)}  pk={mask_value(pk)}  name={d.get('deviceName','?')}")

        selected_device = devices[0]
        if preferred_device_key:
            preferred = str(preferred_device_key).strip()
            for d in devices:
                dk, _pk = _parse_device(d)
                if str(dk).strip() == preferred:
                    selected_device = d
                    log.info(f"[AUTODISCOVERY] Uso dispositivo configurato: {mask_value(dk)}")
                    break
            else:
                raise SystemExit(
                    "[AUTODISCOVERY] device_key configurato non trovato tra i dispositivi dell'account.\n"
                    "Controlla il valore oppure lascialo vuoto per usare il primo dispositivo."
                )

        dk, pk = _parse_device(selected_device)
        if not dk or not pk:
            raise SystemExit(
                f"[AUTODISCOVERY] Struttura dispositivo inattesa: {selected_device}\n"
                "Aggiungi device_key e product_key nel config manualmente."
            )
        device_key, product_key = dk, pk
        log.info(f"[AUTODISCOVERY] device_key={mask_value(device_key)}  product_key={mask_value(product_key)}")

    # ── Salva e applica ───────────────────────────────────────────────────
    discovered = {
        "wf_domain":    domain,
        "device_key":   device_key,
        "product_key":  product_key,
        "accel_client": accel_cli,
    }
    _save_cache(discovered, platform)
    _apply_discovered(discovered)
    log.info("[AUTODISCOVERY] Discovery completata.")
