# Auto-split from original bridge.py
# Module: wf_config
from __future__ import annotations
import os, time, json, logging
import atexit
import logging.handlers
from wf_privacy import mask_value

# -------------------- CONFIG --------------------

def _read_addon_options() -> dict:
    """
    Home Assistant add-on: options are available in /data/options.json
    We use it as fallback when env vars are not passed through by the wrapper.
    """
    try:
        p = "/data/options.json"
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception:
        pass
    return {}

_ADDON_OPTS = _read_addon_options()

def _opt(*keys, default=""):
    for k in keys:
        v = os.getenv(k)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
        # fallback to add-on options.json (usually lowercase keys)
        if k in _ADDON_OPTS and str(_ADDON_OPTS.get(k)).strip() != "":
            return str(_ADDON_OPTS.get(k)).strip()
    return default

# TUTTO dalle variabili d'ambiente - nessun valore hardcodato

WF_EMAIL        = os.getenv("WF_EMAIL", "").strip().lower()
WF_PASSWORD     = os.getenv("WF_PASSWORD", "").strip()
WF_USER_DOMAIN  = os.getenv("WF_DOMAIN", "").strip()

DEVICE_KEY      = os.getenv("DEVICE_KEY", "").strip()
PRODUCT_KEY     = os.getenv("PRODUCT_KEY", "").strip()


# URL MQTT remoto (websocket) e modalità JWT
ACCEL_URL       = os.getenv("ACCEL_URL", "").strip()
CLOUD_JWT_MODE  = os.getenv("CLOUD_JWT_MODE", "password").strip().lower()  # "password" | "username"

# Logging base
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# Keep third-party DEBUG logs from printing full URLs with dk/pk query params.
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("paho").setLevel(logging.WARNING)

_log_path = os.getenv("LOG_FILE", "/data/bridge.log" if os.path.isdir("/data") else "bridge.log")
_log_handler = logging.handlers.RotatingFileHandler(_log_path, maxBytes=1_000_000, backupCount=5)
_log_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logging.getLogger().addHandler(_log_handler)

def _shutdown_logging():
    logging.getLogger().removeHandler(_log_handler)
    _log_handler.close()

atexit.register(_shutdown_logging)
log = logging.getLogger("wf-final-silent-nobeep")

# ---- ACCEL_CLIENT dinamico: prefisso OBBLIGATORIO da env ----
def _auto_accel_client() -> str:
    ts = int(time.time() * 1000)
    val = os.getenv("ACCEL_CLIENT", "").strip()
    if not val:
        raise SystemExit("[CONFIG] Manca ACCEL_CLIENT (accel_client nel config add-on), es: qu_E48279_")
    if not val.endswith("_"):
        val += "_"
    return f"{val}{ts}"

ACCEL_CLIENT = _auto_accel_client()

# MQTT locale
LOCAL_HOST      = os.getenv("LOCAL_HOST", "").strip()
LOCAL_PORT      = int(os.getenv("LOCAL_PORT", "1883"))
LOCAL_USER      = os.getenv("LOCAL_USER", "").strip()
LOCAL_PASS      = os.getenv("LOCAL_PASS", "").strip()

HA_BASE          = os.getenv("HA_BASE", "wonderfree").strip()
DISCOVERY_PREFIX = os.getenv("HA_DISCOVERY", "homeassistant").strip()

# --- URL e PATH SOLO da config ---
BASE_URL           = os.getenv("BASE_URL", "").strip().rstrip("/")
LOGIN_PATH         = os.getenv("LOGIN_PATH", "/v2/enduser/enduserapi/emailPwdLogin").strip()
SECRET_SUFFIX      = os.getenv("SECRET_SUFFIX", "").strip()
REALTIME_ATTRS_URL = os.getenv("REALTIME_ATTRS_URL", "").strip()

# Gestione array da JSON (opzionale)
ATTR_PATHS_JSON = os.getenv("ATTR_PATHS_JSON", "[]").strip()
try:
    ATTR_PATHS = json.loads(ATTR_PATHS_JSON) if ATTR_PATHS_JSON else []
    if not isinstance(ATTR_PATHS, list):
        raise ValueError("non è una lista JSON")
except Exception as e:
    raise SystemExit(f"[CONFIG] ATTR_PATHS_JSON non valido: {e}")

# ---- Validazione parametri OBBLIGATORI ----
# wf_domain, device_key, product_key, accel_client:
#   già impostati da wf_autodiscovery.setup() prima di questo import.
# base_url, accel_url, secret_suffix, realtime_attrs_url:
#   validati in run.sh prima dell'avvio Python.
missing = []
if not WF_EMAIL:          missing.append("WF_EMAIL (wf_email)")
if not WF_PASSWORD:       missing.append("WF_PASSWORD (wf_password)")
if not WF_USER_DOMAIN:    missing.append("WF_DOMAIN (wf_domain)")
if not DEVICE_KEY:        missing.append("DEVICE_KEY (device_key)")
if not PRODUCT_KEY:       missing.append("PRODUCT_KEY (product_key)")
if not BASE_URL:          missing.append("BASE_URL (base_url)")
if not ACCEL_URL:         missing.append("ACCEL_URL (accel_url)")
if not SECRET_SUFFIX:     missing.append("SECRET_SUFFIX (secret_suffix)")
if not REALTIME_ATTRS_URL: missing.append("REALTIME_ATTRS_URL (realtime_attrs_url)")
if not LOCAL_HOST:        missing.append("LOCAL_HOST (mqtt_host)")

if missing:
    raise SystemExit("[CONFIG] Mancano parametri obbligatori: " + ", ".join(missing))

# Protezione: realtime url non deve essere la root del base_url
if REALTIME_ATTRS_URL.rstrip("/") == BASE_URL:
    raise SystemExit("[CONFIG] realtime_attrs_url non può essere uguale a base_url (stai puntando alla root).")

log.info(f"[CONFIG] BASE_URL={BASE_URL}")
log.info(f"[CONFIG] REALTIME_ATTRS_URL={REALTIME_ATTRS_URL}")
log.info(f"[CONFIG] ACCEL_URL={ACCEL_URL}")
log.info(f"[CONFIG] ACCEL_CLIENT={mask_value(ACCEL_CLIENT)}")


# Poll timing (adaptive)
POLL_MIN = int(os.getenv("POLL_MIN", "30"))
POLL_MAX = int(os.getenv("POLL_MAX", "60"))
STARTUP_BURST_SECONDS = int(os.getenv("STARTUP_BURST_SECONDS", "30"))
STARTUP_BURST_PERIOD = int(os.getenv("STARTUP_BURST_PERIOD", "3"))

# --- BUS refresh pacing (anti-ban / anti-spam) ---
REFRESH_MIN = float(os.getenv("REFRESH_MIN", "150"))
REFRESH_MAX = float(os.getenv("REFRESH_MAX", "200"))
REFRESH_MIN_GAP = float(os.getenv("REFRESH_MIN_GAP", "20"))

STARTUP_REFRESH_COUNT = int(os.getenv("STARTUP_REFRESH_COUNT", "1"))
STARTUP_REFRESH_JITTER_MS = int(os.getenv("STARTUP_REFRESH_JITTER_MS", "1500"))
DISABLE_STARTUP_MASK = os.getenv("DISABLE_STARTUP_MASK", "false").strip().lower() in ("1", "true", "yes", "on")
MASK_REFRESH_INTERVAL = float(os.getenv("MASK_REFRESH_INTERVAL", "600"))
REFRESH_HTTP_DELAY_MS = int(os.getenv("REFRESH_HTTP_DELAY_MS", "1800"))
QUICK_REFETCH_AFTER_REFRESH = os.getenv("QUICK_REFETCH_AFTER_REFRESH", "true") in ("1", "true", "True")

# Sanity clamp
if REFRESH_MAX < REFRESH_MIN:
    REFRESH_MAX = REFRESH_MIN
if REFRESH_MIN < 0:
    REFRESH_MIN = 0
if REFRESH_MAX < 0:
    REFRESH_MAX = 0
if REFRESH_MIN_GAP < 0:
    REFRESH_MIN_GAP = 0

# Debounce / optimistic
CMD_GRACE_SECONDS = int(os.getenv("CMD_GRACE_SECONDS", "8"))
SELECT_GRACE_SECONDS = int(os.getenv("SELECT_GRACE_SECONDS", "12"))

# Command routing (anti-beep)
SEND_STRATEGY = os.getenv("SEND_STRATEGY", "auto").strip().lower()  # auto | cloud | local | both
DEDUP_MS = int(os.getenv("DEDUP_MS", "200"))
STALE_SEC = int(os.getenv("STALE_SEC", "250"))
MUTE_POLL = os.getenv("MUTE_POLL", "false") in ("1", "true", "True")
PUBLISH_ONLY_CHANGED = os.getenv("PUBLISH_ONLY_CHANGED", "true") in ("1", "true", "True")

PREFER_HTTP_SOC = os.getenv("PREFER_HTTP_SOC", "false") in ("1", "true", "True")
PREFER_HTTP_TEMP = os.getenv("PREFER_HTTP_TEMP", "false") in ("1", "true", "True")
HTTP_SOC_THRESHOLD = int(os.getenv("HTTP_SOC_THRESHOLD", "5"))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "6.0"))
CLEAR_RETAINED = os.getenv("CLEAR_RETAINED", "false") in ("1", "true", "True")
# Modalità osservatore: nessun comando BUS o switch inviato al device
OBSERVER_ONLY = os.getenv("OBSERVER_ONLY", "false").strip().lower() in ("1", "true", "yes", "on")

# Slider config
OUTPUT_MIN = int(os.getenv("OUTPUT_POWER_MIN", "100"))
OUTPUT_MAX = int(os.getenv("OUTPUT_POWER_MAX", "800"))
OUTPUT_STEP = int(os.getenv("OUTPUT_POWER_STEP", "10"))

APP_HEADERS = {
    "appVersion":    "3.3.1",
    "appSystemType": "android",
    "appId":         "584",
    "Accept":        "application/json",
}

# ... il resto del codice rimane uguale ...

# Availability
AVAIL_TOPIC = os.getenv("AVAIL_TOPIC", f"{HA_BASE}/{DEVICE_KEY}/availability")
AVAIL_PAYLOAD_ON = "online"
AVAIL_PAYLOAD_OFF = "offline"

# Local in/out prefixes
LOCAL_IN_PREFIX  = os.getenv("LOCAL_IN_PREFIX",  "acceleronix/")
LOCAL_OUT_PREFIX = os.getenv("LOCAL_OUT_PREFIX", "acceleronix_out/")

# ------ TOPIC COSTANTI ------
BUS_TOPIC       = f"q/1/d/qd{PRODUCT_KEY}{DEVICE_KEY}/bus"


# Switch topics (base)
LED_CMD_TOPIC       = f"{HA_BASE}/{DEVICE_KEY}/set/led_status"
LED_STATE_TOPIC     = f"{HA_BASE}/{DEVICE_KEY}/state/led_status"
AC_CMD_TOPIC        = f"{HA_BASE}/{DEVICE_KEY}/set/ac_switch"
AC_STATE_TOPIC      = f"{HA_BASE}/{DEVICE_KEY}/state/ac_switch"
DC_CMD_TOPIC        = f"{HA_BASE}/{DEVICE_KEY}/set/dc_switch"
DC_STATE_TOPIC      = f"{HA_BASE}/{DEVICE_KEY}/state/dc_switch"
SCREEN_CMD_TOPIC    = f"{HA_BASE}/{DEVICE_KEY}/set/screen_sleeptime_set"
SCREEN_STATE_TOPIC  = f"{HA_BASE}/{DEVICE_KEY}/state/screen_sleeptime_set"

# Extra switches
GRIDOUT_CMD_TOPIC   = f"{HA_BASE}/{DEVICE_KEY}/set/grid_output"
GRIDOUT_STATE_TOPIC = f"{HA_BASE}/{DEVICE_KEY}/state/grid_output"
BEEP_CMD_TOPIC      = f"{HA_BASE}/{DEVICE_KEY}/set/beep_setting"
BEEP_STATE_TOPIC    = f"{HA_BASE}/{DEVICE_KEY}/state/beep_setting"
SLOWCHG_CMD_TOPIC   = f"{HA_BASE}/{DEVICE_KEY}/set/ac_charging_limit"
SLOWCHG_STATE_TOPIC = f"{HA_BASE}/{DEVICE_KEY}/state/ac_charging_limit"

# Mode select
MODE_CMD_TOPIC      = f"{HA_BASE}/{DEVICE_KEY}/set/mode_set"
MODE_STATE_TOPIC    = f"{HA_BASE}/{DEVICE_KEY}/state/mode_set"

# Slider (number) for on-grid output power
OUTPOW_CMD_TOPIC    = f"{HA_BASE}/{DEVICE_KEY}/set/output_power_set"
OUTPOW_STATE_TOPIC  = f"{HA_BASE}/{DEVICE_KEY}/state/output_power_set"

# Discovery topics
AC_CFG_TOPIC        = f"{DISCOVERY_PREFIX}/switch/wonderfree_{DEVICE_KEY}_ac/config"
DC_CFG_TOPIC        = f"{DISCOVERY_PREFIX}/switch/wonderfree_{DEVICE_KEY}_dc/config"
LED_CFG_TOPIC       = f"{DISCOVERY_PREFIX}/switch/wonderfree_{DEVICE_KEY}_led/config"
SCREEN_CFG_TOPIC    = f"{DISCOVERY_PREFIX}/select/wonderfree_{DEVICE_KEY}_screen_sleeptime_set/config"
GRIDOUT_CFG_TOPIC   = f"{DISCOVERY_PREFIX}/switch/wonderfree_{DEVICE_KEY}_grid_output/config"
BEEP_CFG_TOPIC      = f"{DISCOVERY_PREFIX}/switch/wonderfree_{DEVICE_KEY}_beep/config"
SLOWCHG_CFG_TOPIC   = f"{DISCOVERY_PREFIX}/switch/wonderfree_{DEVICE_KEY}_slowcharge/config"
MODE_CFG_TOPIC      = f"{DISCOVERY_PREFIX}/select/wonderfree_{DEVICE_KEY}_mode/config"
OUTPOW_CFG_TOPIC    = f"{DISCOVERY_PREFIX}/number/wonderfree_{DEVICE_KEY}_output_power_set/config"

# Stato sensori
SENSOR_JSON_TOPIC = f"{HA_BASE}/{DEVICE_KEY}/state"
SENSOR_JSON_RAW_TOPIC = f"{HA_BASE}/{DEVICE_KEY}/state/raw"
SENSOR_DEBUG_TOPIC = f"{HA_BASE}/{DEVICE_KEY}/state/_debug_snapshot"
SENSOR_BASE_TOPIC = f"{HA_BASE}/{DEVICE_KEY}/state"

# -------------------- Helpers --------------------
def hex_bytes(hex_str: str) -> bytes:
    s = hex_str.replace(" ", "").replace("\n", "")
    return bytes.fromhex(s)

def _num(val):
    try:
        if isinstance(val, (int, float)):
            return val
        s = str(val)
        if s.strip() == "":
            return None
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return int(s)
        return float(s)
    except Exception:
        return None


# HEX aggiornati (dalla versione nuova)
LED_ON_HEX   = "AA AA 00 09 A9 02 8F 00 13 01 02 00 01"
LED_OFF_HEX  = "AA AA 00 09 A9 02 90 00 13 01 02 00 00"

AC_ON_HEX    = "AA AA 00 07 A5 00 20 00 13 00 71"
AC_OFF_HEX   = "AA AA 00 07 C7 00 43 00 13 00 70"

DC_ON_HEX    = "AA AA 00 07 17 02 88 00 13 00 79"
DC_OFF_HEX   = "AA AA 00 07 15 02 87 00 13 00 78"

SCREEN_OFF_HEX = "AA AA 00 09 2F 00 DE 00 13 01 32 00 0A"
SCREEN_ON_HEX  = "AA AA 00 09 40 00 F9 00 13 01 32 00 00"

GRID_OFF_HEX = "AA AA 00 07 24 01 2F 00 13 00 E0"
GRID_ON_HEX  = "AA AA 00 07 28 01 32 00 13 00 E1"

BEEP_ON_HEX    = "AA AA 00 07 0C 01 BD 00 13 01 39"
BEEP_OFF_HEX   = "AA AA 00 07 08 01 BA 00 13 01 38"
SLOW_ON_HEX    = "AA AA 00 07 06 01 E7 00 13 01 09"
SLOW_OFF_HEX   = "AA AA 00 07 FD 01 DF 00 13 01 08"


MODE_HEX_BY_LABEL = {
    "PPS":                    "AA AA 00 09 06 00 19 00 13 00 DA 00 00",
    "Micro-Inverter":         "AA AA 00 09 08 00 1A 00 13 00 DA 00 01",
    "Power Reserve Priority": "AA AA 00 09 76 02 84 00 13 00 DA 00 02",
}
MODE_LABEL_BY_VAL = {0: "PPS", 1: "Micro-Inverter", 2: "Power Reserve Priority",}

SCREEN_TIMEOUT_LABEL_BY_VAL = {0: "Never", 3: "3 Mins", 10: "10 Mins"}
SCREEN_TIMEOUT_VAL_BY_LABEL = {v: k for k, v in SCREEN_TIMEOUT_LABEL_BY_VAL.items()}
SCREEN_TIMEOUT_HEX_BY_LABEL = {
    "Never":   SCREEN_ON_HEX,
    "3 Mins":  "AA AA 00 09 41 00 FA 00 13 01 32 00 03",
    "10 Mins": SCREEN_OFF_HEX,
}

DEVICE_STATUS_LABEL = {
    0: "Standby",
    1: "Charge",
    2: "Discharge",
    3: "Charge and Discharge",
    4: "Bypass Mode",
}
