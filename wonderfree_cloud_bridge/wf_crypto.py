# Auto-split from original bridge.py
# Module: wf_crypto
# -------------------- Crypto helpers --------------------
from __future__ import annotations

import base64
import hashlib
import random
import string
from typing import Optional

from Crypto.Cipher import AES

# Import SECRET_SUFFIX from config (it comes from env/options.json)
from wf_config import SECRET_SUFFIX


def _rand(n: int = 16) -> str:
    """Random alfanumerico usato dal login cloud."""
    a = string.ascii_letters + string.digits
    return "".join(random.choice(a) for _ in range(int(n)))


def _pkcs7_pad(b: bytes, block: int = 16) -> bytes:
    pad = block - (len(b) % block)
    return b + bytes([pad]) * pad


def make_pwd(password: str, random_str: str) -> str:
    """Replica la pwd cifrata vista nella app (MD5(random)->mid->AES-CBC->b64)."""
    md5hex = hashlib.md5(random_str.encode("utf-8")).hexdigest()
    mid = md5hex[8:24]
    key = mid.upper().encode("ascii")
    iv = (mid[8:16] + mid[0:8]).upper().encode("ascii")
    ct = AES.new(key, AES.MODE_CBC, iv).encrypt(_pkcs7_pad(password.encode("utf-8"), 16))
    return base64.b64encode(ct).decode("ascii")


def make_signature(email: str, pwd_b64: str, random_str: str, secret_suffix: Optional[str] = None) -> str:
    """Firma SHA256. Compatibile con chiamate a 3 o 4 argomenti."""
    suffix = SECRET_SUFFIX if secret_suffix is None else str(secret_suffix)
    s = f"{email}{pwd_b64}{random_str}{suffix}"
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def normalize_bearer(tok: str) -> str:
    tok = (tok or "").strip()
    return tok if tok.lower().startswith("bearer ") else ("Bearer " + tok if tok else "")
