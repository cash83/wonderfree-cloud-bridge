import os, time, json, uuid, random, string, hashlib, base64, logging, threading, urllib.parse
from typing import Optional, Dict, Any
import requests
import paho.mqtt.client as mqtt
from Crypto.Cipher import AES

# -------------------- CONFIG --------------------
WF_EMAIL        = os.getenv("WF_EMAIL",        "")
WF_PASSWORD     = os.getenv("WF_PASSWORD",     "")
WF_USER_DOMAIN  = os.getenv("WF_DOMAIN",       "E.SP.4294967410")
DEVICE_KEY      = os.getenv("DEVICE_KEY",      "")
PRODUCT_KEY     = os.getenv("PRODUCT_KEY",     "p11qXo")
ACCEL_URL       = os.getenv("ACCEL_URL",       "wss://iot-south.acceleronix.io:8443/ws/v2")
ACCEL_CLIENT    = os.getenv("ACCEL_CLIENT",    f"qu_{uuid.uuid4().hex[:6].upper()}_{int(time.time()*1000)}")
CLOUD_JWT_MODE  = os.getenv("CLOUD_JWT_MODE",  "password").strip().lower()
LOCAL_HOST      = os.getenv("LOCAL_HOST",      "core-mosquitto")
LOCAL_PORT      = int(os.getenv("LOCAL_PORT",  "1883"))
LOCAL_USER      = os.getenv("LOCAL_USER",      "")
LOCAL_PASS      = os.getenv("LOCAL_PASS",      "")
HA_BASE         = os.getenv("HA_BASE",         "oukitel")
DISCOVERY_PREFIX= os.getenv("HA_DISCOVERY",    "homeassistant")

LOG_LEVEL       = os.getenv("LOG_LEVEL",       "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("oukitel-bridge")

BASE_URL        = "https://iot-api.acceleronix.io"
LOGIN_PATH      = "/v2/enduser/enduserapi/emailPwdLogin"
SECRET_SUFFIX   = "3aRNUwWahjyANa7WfBK2wCCkxCexB6nXxKJwXxfePvzf"

ATTR_PATHS = [
    "/v2/binding/enduserapi/getDeviceBusinessAttributes",
    "/v2/enduser/enduserapi/getDeviceBusinessAttributes",
]

POLL_MIN = int(os.getenv("POLL_MIN", "2"))
POLL_MAX = int(os.getenv("POLL_MAX", "20"))
STARTUP_BURST_SECONDS = int(os.getenv("STARTUP_BURST_SECONDS", "60"))
STARTUP_BURST_PERIOD  = int(os.getenv("STARTUP_BURST_PERIOD", "2"))
CMD_GRACE_SECONDS = int(os.getenv("CMD_GRACE_SECONDS", "6"))
SEND_STRATEGY = os.getenv("SEND_STRATEGY", "auto").lower()
DEDUP_MS = int(os.getenv("DEDUP_MS", "400"))
STALE_SEC = int(os.getenv("STALE_SEC", "60"))
MUTE_POLL = os.getenv("MUTE_POLL", "1") in ("1","true","True")
PUBLISH_ONLY_CHANGED = os.getenv("PUBLISH_ONLY_CHANGED", "0") in ("1","true","True")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "6.0"))
CLEAR_RETAINED = os.getenv("CLEAR_RETAINED", "0") in ("1","true","True")

APP_HEADERS = {
    "appVersion":    "2.18.0",
    "appSystemType": "android",
    "appId":         "277",
    "Accept":        "application/json",
}

AVAIL_TOPIC = f"{HA_BASE}/availability"
AVAIL_PAYLOAD_ON = "online"
AVAIL_PAYLOAD_OFF = "offline"

LOCAL_OUT_PREFIX = os.getenv("LOCAL_OUT_PREFIX", "acceleronix_out/")
BUS_TOPIC = f"q/1/d/qd{PRODUCT_KEY}{DEVICE_KEY}/bus"

# NOTE: These HEX commands are NOT verified on real hardware.
# AC/DC control is currently non-functional — waiting for Frida capture.
# Do NOT rely on these values. Contributions welcome.
AC_ON_HEX  = "AA AA 00 07 74 00 07 00 13 01 59"
AC_OFF_HEX = "AA AA 00 17 FF 00 0E 00 13 00 34 00 03 00 12 00 00 00 1A 00 00 00 22 00 00 01 58"
DC_ON_HEX  = "AA AA 00 07 95 00 10 00 13 01 71"
DC_OFF_HEX = "AA AA 00 2F AE 00 13 00 13 00 4C 00 09 00 12 00 00 00 1A 00 00 00 22 00 00 00 2A 00 00 00 32 00 00 00 3A 00 00 00 42 00 00 00 4A 00 00 00 52 00 00 01 70"

# -------------------- Crypto --------------------
def _rand(n=16):
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n))

def _pkcs7_pad(b, block=16):
    pad = block - (len(b) % block)
    return b + bytes([pad]) * pad

def make_pwd(password, random_str):
    md5hex = hashlib.md5(random_str.encode()).hexdigest()
    mid = md5hex[8:24]
    key = mid.upper().encode("ascii")
    iv  = (mid[8:16] + mid[0:8]).upper().encode("ascii")
    ct  = AES.new(key, AES.MODE_CBC, iv).encrypt(_pkcs7_pad(password.encode(), 16))
    return base64.b64encode(ct).decode()

def make_signature(email, pwd_b64, random_str):
    s = f"{email}{pwd_b64}{random_str}{SECRET_SUFFIX}"
    return hashlib.sha256(s.encode()).hexdigest()

def normalize_bearer(tok):
    tok = tok.strip()
    return tok if tok.lower().startswith("bearer ") else ("Bearer " + tok)

# -------------------- Token --------------------
class TokenManager:
    def __init__(self, email, password, user_domain):
        self.email = email
        self.password = password
        self.user_domain = user_domain
        self.access = ""
        self.exp = 0

    def _api(self):
        s = requests.Session()
        for k, v in APP_HEADERS.items():
            s.headers[k] = v
        s.headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        return s

    def login(self):
        rnd = _rand(16)
        pwd_b64 = make_pwd(self.password, rnd)
        sig = make_signature(self.email, pwd_b64, rnd)
        data = {"email": self.email, "pwd": pwd_b64, "random": rnd,
                "userDomain": self.user_domain, "signature": sig}
        r = self._api().post(BASE_URL + LOGIN_PATH, data=data, timeout=25)
        j = r.json()
        if r.status_code != 200 or j.get("code") != 200:
            raise RuntimeError(f"Login failed: {j}")
        tok = j["data"]
        self.access = str(tok["accessToken"]["token"])
        self.exp    = int(tok["accessToken"]["expirationTime"])
        log.info("Login OK; token acquired.")

    def ensure(self, skew=180):
        if self.access and int(time.time()) < self.exp - skew:
            return self.access
        self.login()
        return self.access

# -------------------- Helpers --------------------
def hex_bytes(hex_str):
    return bytes.fromhex(hex_str.replace(" ", ""))

def _num(val):
    try:
        if isinstance(val, (int, float)): return val
        s = str(val)
        return int(s) if s.isdigit() else float(s)
    except Exception:
        return None

# -------------------- Bridge --------------------
class Bridge:
    def __init__(self):
        u = urllib.parse.urlparse(ACCEL_URL)
        self.rhost = u.hostname
        self.rport = u.port or (443 if u.scheme == "wss" else 80)
        self.rpath = u.path or "/ws/v2"
        self.token_mgr = TokenManager(WF_EMAIL, WF_PASSWORD, WF_USER_DOMAIN)
        self.remote = None
        self.local  = None
        self.stop = threading.Event()
        self.pending: Dict[str, Dict[str, Any]] = {}
        self.start_t = time.time()
        self.adaptive_period = POLL_MIN
        self._last_cmd_hash = None
        self._last_cmd_time = 0.0
        self.last_publish_ts = 0
        self._SENSOR_JSON_TOPIC = f"{HA_BASE}/{DEVICE_KEY}/state"
        self._AC_STATE_TOPIC = f"{HA_BASE}/{DEVICE_KEY}/state/ac_switch"
        self._DC_STATE_TOPIC = f"{HA_BASE}/{DEVICE_KEY}/state/dc_switch"

    def _on_local_connect(self, client, userdata, flags, rc, properties=None):
        if rc != 0:
            log.error(f"LOCAL connect failed rc={rc}"); return
        log.info(f"LOCAL connected {LOCAL_HOST}:{LOCAL_PORT}")
        client.will_set(AVAIL_TOPIC, payload=AVAIL_PAYLOAD_OFF, retain=True)
        client.publish(AVAIL_TOPIC, AVAIL_PAYLOAD_ON, qos=0, retain=True)
        client.subscribe(f"{HA_BASE}/{DEVICE_KEY}/set/#", qos=0)
        self._publish_discovery(client)

    def _publish_discovery(self, client):
        base_dev = {
            "identifiers": [f"oukitel_{DEVICE_KEY}"],
            "manufacturer": "Oukitel",
            "model": PRODUCT_KEY,
            "name": "Oukitel Power Station"
        }

        def pub_cfg(topic, payload):
            payload["availability_topic"] = AVAIL_TOPIC
            payload["payload_available"]  = AVAIL_PAYLOAD_ON
            payload["payload_not_available"] = AVAIL_PAYLOAD_OFF
            client.publish(topic, json.dumps(payload).encode(), qos=0, retain=True)

        AC_CMD = f"{HA_BASE}/{DEVICE_KEY}/set/ac_switch"
        DC_CMD = f"{HA_BASE}/{DEVICE_KEY}/set/dc_switch"

        # NOTE: AC/DC switches published but NOT functional (HEX unverified)
        pub_cfg(f"{DISCOVERY_PREFIX}/switch/oukitel_ac_{DEVICE_KEY}/config", {
            "name": "AC Switch", "uniq_id": f"oukitel_ac_{DEVICE_KEY}",
            "cmd_t": AC_CMD, "stat_t": self._AC_STATE_TOPIC,
            "pl_on": "ON", "pl_off": "OFF",
            "icon": "mdi:power-plug-outline", "device": base_dev})
        client.publish(self._AC_STATE_TOPIC, b"OFF", qos=0, retain=True)

        pub_cfg(f"{DISCOVERY_PREFIX}/switch/oukitel_dc_{DEVICE_KEY}/config", {
            "name": "DC Switch", "uniq_id": f"oukitel_dc_{DEVICE_KEY}",
            "cmd_t": DC_CMD, "stat_t": self._DC_STATE_TOPIC,
            "pl_on": "ON", "pl_off": "OFF",
            "icon": "mdi:current-dc", "device": base_dev})
        client.publish(self._DC_STATE_TOPIC, b"OFF", qos=0, retain=True)

        SENSOR_TOPIC = self._SENSOR_JSON_TOPIC

        def disc_sensor(obj_id, name, unit=None, device_class=None, state_class=None):
            uniq = f"oukitel_{DEVICE_KEY}_{obj_id}"
            cfg = {
                "name": name, "uniq_id": uniq,
                "state_topic": SENSOR_TOPIC,
                "value_template": "{{ value_json.%s }}" % obj_id,
                "device": base_dev,
                "availability_topic": AVAIL_TOPIC,
                "payload_available": AVAIL_PAYLOAD_ON,
                "payload_not_available": AVAIL_PAYLOAD_OFF,
            }
            if unit: cfg["unit_of_measurement"] = unit
            if device_class: cfg["device_class"] = device_class
            if state_class: cfg["state_class"] = state_class
            client.publish(f"{DISCOVERY_PREFIX}/sensor/{uniq}/config",
                           json.dumps(cfg).encode(), qos=0, retain=True)

        sensors = [
            ("battery_percentage",    "Battery Capacity",            "%",   "battery",     "measurement"),
            ("remain_time",           "Remaining Available Time",     "min", None,          "measurement"),
            ("remain_time_h",         "Remaining Time (h)",           "h",   None,          "measurement"),
            ("remain_charging_time",  "Remaining Charging Time",      "min", None,          "measurement"),
            ("temp",                  "Device Temperature",           "°C",  "temperature", "measurement"),
            ("total_input_power",     "Total Input Power",            "W",   "power",       "measurement"),
            ("total_output_power",    "Total Output Power",           "W",   "power",       "measurement"),
            ("ac_input",              "AC Charging Input Power",      "W",   "power",       "measurement"),
            ("dc_input",              "PV Charging Input Power",      "W",   "power",       "measurement"),
            ("ac1_output",            "AC1 Output Power",             "W",   "power",       "measurement"),
            ("ac1_output_voltage",    "AC1 Output Voltage",           "V",   "voltage",     "measurement"),
            ("car1_output",           "12V Output Power",             "W",   "power",       "measurement"),
            ("watt_24v",              "24V Output Power",             "W",   "power",       "measurement"),
            ("usb_switch",            "USB Switch",                   None,  None,          None),
            ("USB_A_output",          "USB-A Output Power",           "W",   "power",       "measurement"),
            ("USB_QC1_output",        "USB-C1 Output Power",          "W",   "power",       "measurement"),
            ("USB_QC2_output",        "USB-C2 Output Power",          "W",   "power",       "measurement"),
            ("Typec1_output",         "TypeC1 Output Power",          "W",   "power",       "measurement"),
            ("Typec2_output",         "TypeC2 Output Power",          "W",   "power",       "measurement"),
            ("AC_Version",            "Inverter Version",             None,  None,          None),
            ("Fan_Switch",            "Cooling Fan",                  None,  None,          None),
            ("ACvoltage_Switchover",  "Output Voltage Setting",       "V",   None,          None),
            ("Frequency_Switchover",  "Output Frequency Setting",     "Hz",  None,          None),
            ("ac_charging_limit",     "Max AC Charging Power",        "%",   None,          None),
            ("Parallel",              "Parallel Machines",            None,  None,          None),
            ("updated_at",            "Last Updated",                 "s",   None,          None),
        ]
        for s in sensors:
            disc_sensor(*s)

    def _route_and_publish(self, payload):
        h = hashlib.md5(payload).hexdigest()
        now = time.time()
        if self._last_cmd_hash == h and (now - self._last_cmd_time) * 1000 < DEDUP_MS:
            return
        self._last_cmd_hash = h
        self._last_cmd_time = now
        if SEND_STRATEGY == "cloud":
            if self.remote: self.remote.publish(BUS_TOPIC, payload=payload, qos=0, retain=False)
        elif SEND_STRATEGY == "local":
            if self.local: self.local.publish(LOCAL_OUT_PREFIX + BUS_TOPIC, payload, qos=0, retain=False)
        elif SEND_STRATEGY == "both":
            if self.local: self.local.publish(LOCAL_OUT_PREFIX + BUS_TOPIC, payload, qos=0, retain=False)
            if self.remote: self.remote.publish(BUS_TOPIC, payload=payload, qos=0, retain=False)
        else:  # auto
            if self.remote:
                self.remote.publish(BUS_TOPIC, payload=payload, qos=0, retain=False)
            elif self.local:
                self.local.publish(LOCAL_OUT_PREFIX + BUS_TOPIC, payload, qos=0, retain=False)

    def _on_local_message(self, client, userdata, msg):
        t = msg.topic
        p = msg.payload.decode(errors="ignore").strip()
        if t.endswith("/ac_switch"):
            on = p.upper() == "ON"
            log.warning("AC switch command received — HEX NOT VERIFIED, may not work on hardware")
            self._route_and_publish(hex_bytes(AC_ON_HEX if on else AC_OFF_HEX))
            self.pending["ac_switch"] = {"desired": on, "until": time.time() + CMD_GRACE_SECONDS}
            try: client.publish(self._AC_STATE_TOPIC, b"ON" if on else b"OFF", qos=0, retain=True)
            except: pass
        elif t.endswith("/dc_switch"):
            on = p.upper() == "ON"
            log.warning("DC switch command received — HEX NOT VERIFIED, may not work on hardware")
            self._route_and_publish(hex_bytes(DC_ON_HEX if on else DC_OFF_HEX))
            self.pending["dc_switch"] = {"desired": on, "until": time.time() + CMD_GRACE_SECONDS}
            try: client.publish(self._DC_STATE_TOPIC, b"ON" if on else b"OFF", qos=0, retain=True)
            except: pass

    def _on_remote_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            log.info(f"REMOTE connected {self.rhost}:{self.rport}{self.rpath}")
        else:
            log.warning(f"REMOTE connect failed rc={rc}")

    def _connect_remote(self):
        tok = self.token_mgr.ensure()
        jwt = normalize_bearer(tok)
        cli = mqtt.Client(client_id=ACCEL_CLIENT, transport="websockets", protocol=mqtt.MQTTv311)
        if CLOUD_JWT_MODE == "username":
            cli.username_pw_set(username=jwt, password="")
        else:
            cli.username_pw_set(username="", password=jwt)
        cli.ws_set_options(path=self.rpath)
        cli.tls_set()
        cli.on_connect = self._on_remote_connect
        cli.connect(self.rhost, self.rport, keepalive=25)
        cli.loop_start()
        self.remote = cli

    def _connect_local(self):
        try:
            from paho.mqtt.client import CallbackAPIVersion
            cli = mqtt.Client(CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311)
        except Exception:
            cli = mqtt.Client(protocol=mqtt.MQTTv311)
        if LOCAL_USER:
            cli.username_pw_set(LOCAL_USER, LOCAL_PASS or "")
        cli.on_connect = self._on_local_connect
        cli.on_message = self._on_local_message
        cli.connect(LOCAL_HOST, LOCAL_PORT, keepalive=60)
        cli.loop_start()
        self.local = cli

    def _fetch_attrs_raw(self, session):
        params_list = [
            {"dk": DEVICE_KEY, "pk": PRODUCT_KEY},
            {"deviceKey": DEVICE_KEY, "productKey": PRODUCT_KEY}
        ]
        for path in ATTR_PATHS:
            for params in params_list:
                try:
                    r = session.get(BASE_URL + path, params=params, timeout=15)
                    if r.status_code != 200: continue
                    j = r.json()
                    if j.get("code") == 200:
                        data = j.get("data") or {}
                        devd = data.get("deviceData") or {}
                        kv = self._parse_tsl(data.get("customizeTslInfo") or [])
                        return kv, devd.get("updateTime")
                except Exception:
                    pass
        return None, None

    def _parse_tsl(self, tsl_list):
        kv = {}
        for item in tsl_list or []:
            code = item.get("resourceCode")
            val  = item.get("resourceValce")
            if not code: continue
            if isinstance(val, str):
                v = val.strip()
                if v.startswith("{") and v.endswith("}"):
                    try: kv[code] = json.loads(v); continue
                    except: pass
            kv[code] = val

        def _int(k):
            if k in kv:
                try: kv[k] = int(float(kv[k]))
                except: pass

        def _bool(k):
            if k in kv:
                kv[k] = str(kv[k]).strip().lower() in ("true", "1", "on")

        for n in ["battery_percentage","total_input_power","total_output_power","ac_input",
                  "dc_input","remain_time","remain_charging_time","ac_charging_limit","Parallel"]:
            _int(n)
        for n in ["ac_switch","dc_switch","usb_switch","Fan_Switch","Aboot_Mode"]:
            _bool(n)
        if "temp" in kv:
            try: kv["temp"] = round(float(kv["temp"]), 1)
            except: pass
        return kv

    def _normalize_state(self, kv, dev_update):
        st = {}
        for k in ("battery_percentage","remain_time","remain_charging_time","temp",
                  "total_input_power","total_output_power","ac_input","dc_input",
                  "ac_switch","dc_switch","usb_switch","Fan_Switch","Aboot_Mode",
                  "AC_Version","ACvoltage_Switchover","Frequency_Switchover",
                  "ac_charging_limit","Parallel"):
            if k in kv: st[k] = kv[k]

        ac = kv.get("ac_data") or {}
        if isinstance(ac, dict):
            st["ac1_output"]         = _num(ac.get("ac1_output"))
            st["ac1_output_voltage"] = _num(ac.get("ac1_output_voltage"))

        dc = kv.get("dc_data") or {}
        if isinstance(dc, dict):
            st["car1_output"] = _num(dc.get("car1_output"))
            st["watt_24v"]    = _num(dc.get("watt_24v"))

        usb = kv.get("usb_data") or {}
        if isinstance(usb, dict):
            st["USB_A_output"]   = _num(usb.get("USB_A_output"))
            st["USB_QC1_output"] = _num(usb.get("USB_QC1_output"))
            st["USB_QC2_output"] = _num(usb.get("USB_QC2_output"))

        tc = kv.get("typec_data") or {}
        if isinstance(tc, dict):
            st["Typec1_output"] = _num(tc.get("Typec1_output"))
            st["Typec2_output"] = _num(tc.get("Typec2_output"))

        st["updated_at"] = int(time.time())
        try:
            rem = st.get("remain_time")
            st["remain_time_h"] = round(float(rem) / 60.0, 2) if rem is not None else None
        except: st["remain_time_h"] = None

        return st

    def _publish_state(self, st):
        try:
            if self.local:
                self.local.publish(self._SENSOR_JSON_TOPIC,
                                   json.dumps(st).encode(), qos=0, retain=True)
                base = f"{HA_BASE}/{DEVICE_KEY}/state"
                for k, v in st.items():
                    if v is None: payload = b""
                    elif isinstance(v, (dict, list)): payload = json.dumps(v).encode()
                    else: payload = str(v).encode()
                    self.local.publish(f"{base}/{k}", payload, qos=0, retain=True)
            self.last_publish_ts = st.get("updated_at", 0)
        except Exception:
            pass

    def _reconcile_switches(self, st):
        now = time.time()
        def maybe_pub(key, topic, val):
            pend = self.pending.get(key)
            if pend and now < pend["until"]:
                if val is not None and val == bool(pend["desired"]):
                    self.pending.pop(key, None)
                return
            if val is not None and self.local:
                self.local.publish(topic, b"ON" if val else b"OFF", qos=0, retain=True)
            if pend and now >= pend["until"]:
                self.pending.pop(key, None)

        maybe_pub("ac_switch", self._AC_STATE_TOPIC, st.get("ac_switch"))
        maybe_pub("dc_switch", self._DC_STATE_TOPIC, st.get("dc_switch"))

    def _poll_loop(self):
        sess = requests.Session()
        for k, v in APP_HEADERS.items():
            sess.headers[k] = v
        last_digest = None
        while not self.stop.is_set():
            now = time.time()
            period = STARTUP_BURST_PERIOD if now - self.start_t <= STARTUP_BURST_SECONDS else self.adaptive_period
            try:
                sess.headers["Authorization"] = normalize_bearer(self.token_mgr.ensure())
                kv, dev_update = self._fetch_attrs_raw(sess)
                if kv and self.local:
                    st = self._normalize_state(kv, dev_update)
                    self._publish_state(st)
                    self._reconcile_switches(st)
                    digest = hashlib.md5(json.dumps(st, sort_keys=True).encode()).hexdigest()
                    if digest != last_digest:
                        period = max(POLL_MIN, period // 2)
                    else:
                        period = min(POLL_MAX, period + 2)
                    last_digest = digest
                    self.adaptive_period = period
                    if not MUTE_POLL:
                        log.info(f"[POLL] battery={st.get('battery_percentage')}% "
                                 f"in={st.get('total_input_power')}W "
                                 f"out={st.get('total_output_power')}W next={period}s")
                else:
                    log.warning("[POLL] no data received")
            except Exception as e:
                log.warning(f"[POLL ERR] {e}")
            time.sleep(period)

    def _watchdog_loop(self):
        while not self.stop.is_set():
            try:
                now = int(time.time())
                avail = AVAIL_PAYLOAD_ON if (self.last_publish_ts and now - self.last_publish_ts <= STALE_SEC) else AVAIL_PAYLOAD_OFF
                if self.local:
                    self.local.publish(AVAIL_TOPIC, avail, qos=0, retain=True)
            except Exception:
                pass
            time.sleep(5)

    def start(self):
        self.token_mgr.ensure()
        self._connect_local()
        self._connect_remote()
        threading.Thread(target=self._poll_loop, daemon=True).start()
        threading.Thread(target=self._watchdog_loop, daemon=True).start()
        log.info(f"Oukitel bridge started. strategy={SEND_STRATEGY.upper()}")

    def run_forever(self):
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

if __name__ == "__main__":
    b = Bridge()
    b.start()
    b.run_forever()
