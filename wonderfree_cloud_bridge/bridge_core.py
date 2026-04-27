# Auto-split from original bridge.py
# Module: bridge_core
from __future__ import annotations

from wf_config import *
from wf_token import TokenManager

import json
import time
import uuid
import threading
import urllib.parse
from typing import Any, Dict, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

class Bridge:

    def __init__(self):
        u = urllib.parse.urlparse(ACCEL_URL)
        if u.scheme not in ("ws", "wss"):
            raise ValueError("ACCEL_URL must be ws:// or wss://")
        self.rhost = u.hostname or "iot-south.acceleronix.io"
        self.rport = u.port or (443 if u.scheme == "wss" else 80)
        self.rpath = u.path or "/ws/v2"

        self.token_mgr = TokenManager(WF_EMAIL, WF_PASSWORD, WF_USER_DOMAIN)
        self.remote = None
        self.local  = None
        self.stop = threading.Event()

        self.pending: Dict[str, Dict[str, Any]] = {}
        self.next_poll = time.time()
        self.adaptive_period = POLL_MIN
        self.start_t = time.time()
        self._last_cmd_hash = None
        self._last_cmd_time = 0.0
        self.last_publish_ts = 0
        self._last_published = {}

        # Availability state
        self._pending_offline_since = 0.0
        self._device_force_offline = False
        self._onl_declared_offline = False
        self._last_ack_ts = 0.0
        self._cloud_online_status = None
        self._last_cloud_status_check = 0.0
        self._device_status_last_detail = {}

    def _on_local_connect(self, client, userdata, flags, rc, properties=None):
        if rc != 0:
            log.error(f"LOCAL connect failed rc={rc}")
            return
        log.info(f"LOCAL connected {LOCAL_HOST}:{LOCAL_PORT}")
        client.will_set(AVAIL_TOPIC, AVAIL_PAYLOAD_OFF, retain=True)
        # Non pubblicare subito ONLINE al connect locale: aspetta la prima valutazione reale
        # (cloud status / watchdog) per evitare retained online errati allo startup.

        client.subscribe(f"{HA_BASE}/{DEVICE_KEY}/set/#", qos=0)
        client.subscribe(f"{LOCAL_OUT_PREFIX}#", qos=0)

        base_dev = {
            "identifiers": [f"wonderfree_{DEVICE_KEY}"],
            "manufacturer": "Wonderfree",
            "model": PRODUCT_KEY,
            "name": "Wonderfree Power Station"
        }

        def publish_cfg(topic, payload):
            payload["availability_topic"] = AVAIL_TOPIC
            payload["payload_available"] = AVAIL_PAYLOAD_ON
            payload["payload_not_available"] = AVAIL_PAYLOAD_OFF
            client.publish(topic, json.dumps(payload).encode(), qos=0, retain=True)

        publish_cfg(AC_CFG_TOPIC, {"name":"AC Output","uniq_id":f"wf_ac_{DEVICE_KEY}","cmd_t":AC_CMD_TOPIC,"stat_t":AC_STATE_TOPIC,"pl_on":"ON","pl_off":"OFF","icon":"mdi:power-plug-outline","device":base_dev})
        publish_cfg(DC_CFG_TOPIC, {"name":"DC Output","uniq_id":f"wf_dc_{DEVICE_KEY}","cmd_t":DC_CMD_TOPIC,"stat_t":DC_STATE_TOPIC,"pl_on":"ON","pl_off":"OFF","icon":"mdi:current-dc","device":base_dev})
        publish_cfg(LED_CFG_TOPIC, {"name":"LED","uniq_id":f"wf_led_{DEVICE_KEY}","cmd_t":LED_CMD_TOPIC,"stat_t":LED_STATE_TOPIC,"pl_on":"ON","pl_off":"OFF","icon":"mdi:flashlight","device":base_dev})
        publish_cfg(SCREEN_CFG_TOPIC, {"name":"Screen Off Time","uniq_id":f"wf_screen_timeout_{DEVICE_KEY}","cmd_t":SCREEN_CMD_TOPIC,"stat_t":SCREEN_STATE_TOPIC,"options":["Never","3 Mins","10 Mins"],"icon":"mdi:monitor","device":base_dev})
        # Non pubblicare un default finto per lo screen timeout: aspetta lo stato reale dal cloud.
        # Pulisci anche la vecchia discovery come switch, se ancora presente da versioni precedenti.
        client.publish(f"{DISCOVERY_PREFIX}/switch/wonderfree_screen/config", b"", qos=0, retain=True)
        publish_cfg(GRIDOUT_CFG_TOPIC, {"name":"On-grid Output Switch","uniq_id":f"wf_grid_output_{DEVICE_KEY}","cmd_t":GRIDOUT_CMD_TOPIC,"stat_t":GRIDOUT_STATE_TOPIC,"pl_on":"ON","pl_off":"OFF","icon":"mdi:transmission-tower-export","device":base_dev})
        publish_cfg(BEEP_CFG_TOPIC, {"name":"Buzzer","uniq_id":f"wf_beep_{DEVICE_KEY}","cmd_t":BEEP_CMD_TOPIC,"stat_t":BEEP_STATE_TOPIC,"pl_on":"ON","pl_off":"OFF","icon":"mdi:volume-high","device":base_dev})
        publish_cfg(SLOWCHG_CFG_TOPIC, {"name":"Silent Charge","uniq_id":f"wf_slowchg_{DEVICE_KEY}","cmd_t":SLOWCHG_CMD_TOPIC,"stat_t":SLOWCHG_STATE_TOPIC,"pl_on":"ON","pl_off":"OFF","icon":"mdi:tortoise","device":base_dev})
        publish_cfg(MODE_CFG_TOPIC, {"name":"Working Mode","uniq_id":f"wf_mode_{DEVICE_KEY}","cmd_t":MODE_CMD_TOPIC,"stat_t":MODE_STATE_TOPIC,"options":["PPS","Micro-Inverter","Power Reserve Priority"],"icon":"mdi:transmission-tower-import","device":base_dev})
        publish_cfg(OUTPOW_CFG_TOPIC, {"name":"On-grid Power Setting","uniq_id":f"wf_output_power_{DEVICE_KEY}","cmd_t":OUTPOW_CMD_TOPIC,"stat_t":OUTPOW_STATE_TOPIC,"min": OUTPUT_MIN, "max": OUTPUT_MAX, "step": OUTPUT_STEP,"unit_of_measurement":"W","device_class":"power","icon":"mdi:lightning-bolt","device": base_dev})

        def disc_sensor(obj_id, name, unit=None, device_class=None, state_class=None):
            uniq = f"wonderfree_{DEVICE_KEY}_{obj_id}"
            cfg_topic = f"{DISCOVERY_PREFIX}/sensor/{uniq}/config"
            cfg = {
                "name": name, "uniq_id": uniq,
                "state_topic": SENSOR_JSON_TOPIC,
                "value_template": "{{ value_json.%s }}" % obj_id,
                "device": base_dev,
                "availability_topic": AVAIL_TOPIC,
                "payload_available": AVAIL_PAYLOAD_ON,
                "payload_not_available": AVAIL_PAYLOAD_OFF,
            }
            if unit: cfg["unit_of_measurement"] = unit
            if device_class: cfg["device_class"] = device_class
            if state_class: cfg["state_class"] = state_class
            client.publish(cfg_topic, json.dumps(cfg).encode(), qos=0, retain=True)

        for s in [
            ("battery_percentage", "Battery", "%", "battery", "measurement"),
            ("remaining_time", "Remaining Time (hours)", "h", None, "measurement"),
            ("charge_time_to_full_str", "Charge Time to Full (formatted)", None, None, None),
            ("battery_voltage", "Battery Voltage", "V", "voltage", "measurement"),
            ("battery_current", "Battery Current", "A", "current", "measurement"),
            ("battery_temp", "Battery Temperature", "°C", "temperature", "measurement"),
            ("ac_input_power",  "AC Input Power (Charging)", "W", "power", "measurement"),
            ("ac_output_power", "AC Output Power (Discharging)", "W", "power", "measurement"),
            ("ac_output_voltage", "AC Output Voltage", "V", "voltage", "measurement"),
            ("ac_output_current", "AC Output Current", "A", "current", "measurement"),
            ("pv_input_power", "PV Input Power (Solar)", "W", "power", "measurement"),
            ("pv_input_energy","PV Input Energy", "kWh", "energy", "total_increasing"),
            ("dc_input_power", "DC Input Power", "W", "power", "measurement"),
            ("total_input_power","Total Input Power", "W", "power", "measurement"),
            ("total_output_power","Total Output Power", "W", "power", "measurement"),
            ("grid_voltage","Grid Voltage","V","voltage","measurement"),
            ("grid_freq","Grid Frequency","Hz",None,"measurement"),
            ("dc24v_voltage","DC 24V Voltage","V","voltage","measurement"),
            ("dc24v_current","DC 24V Current","A","current","measurement"),
            ("dc12v1_voltage","DC 12V1 Voltage","V","voltage","measurement"),
            ("dc12v1_current","DC 12V1 Current","A","current","measurement"),
            ("dc12v2_voltage","DC 12V2 Voltage","V","voltage","measurement"),
            ("dc12v2_current","DC 12V2 Current","A","current","measurement"),
            ("typec_1_voltage","Type-C1 Voltage","V","voltage","measurement"),
            ("typec_1_current","Type-C1 Current","A","current","measurement"),
            ("typec_1_power","Type-C1 Power","W","power","measurement"),
            ("typec_2_voltage","Type-C2 Voltage","V","voltage","measurement"),
            ("typec_2_current","Type-C2 Current","A","current","measurement"),
            ("typec_2_power","Type-C2 Power","W","power","measurement"),
            ("usb_a1_voltage","USB-A1 Voltage","V","voltage","measurement"),
            ("usb_a1_current","USB-A1 Current","A","current","measurement"),
            ("usb_a1_power","USB-A1 Power","W","power","measurement"),
            ("usb_a2_voltage","USB-A2 Voltage","V","voltage","measurement"),
            ("usb_a2_current","USB-A2 Current","A","current","measurement"),
            ("usb_a2_power","USB-A2 Power","W","power","measurement"),
            ("usb_a3_voltage","USB-A3 Voltage","V","voltage","measurement"),
            ("usb_a3_current","USB-A3 Current","A","current","measurement"),
            ("usb_a3_power","USB-A3 Power","W","power","measurement"),
            ("usb_a4_voltage","USB-A4 Voltage","V","voltage","measurement"),
            ("usb_a4_current","USB-A4 Current","A","current","measurement"),
            ("usb_a4_power","USB-A4 Power","W","power","measurement"),
            ("temp_bms","BMS Temperature","°C","temperature","measurement"),
            ("temp_inv","Inverter Temperature","°C","temperature","measurement"),
            ("temp_mppt","MPPT Temperature","°C","temperature","measurement"),
            ("signal_strength","Signal Strength","dBm","signal_strength","measurement"),
            ("fault_code","Fault Code",None,None,None),
            ("device_status","Device Status",None,None,None),
            ("output_power_set","On-Grid Power Set (W)","W","power","measurement"),
        ]:
            disc_sensor(*s)

        def publish_cfg2(topic, payload):
            payload["availability_topic"] = AVAIL_TOPIC
            payload["payload_available"] = AVAIL_PAYLOAD_ON
            payload["payload_not_available"] = AVAIL_PAYLOAD_OFF
            client.publish(topic, json.dumps(payload).encode(), qos=0, retain=True)

        publish_cfg2(f"{DISCOVERY_PREFIX}/sensor/wonderfree_{DEVICE_KEY}_bridge_uptime/config", {
            "name": "Bridge Uptime (min)",
            "uniq_id": f"wonderfree_{DEVICE_KEY}_bridge_uptime",
            "state_topic": f"{SENSOR_BASE_TOPIC}/bridge_uptime",
            "unit_of_measurement": "min",
            "state_class": "measurement",
            "device": base_dev,
        })
        publish_cfg2(f"{DISCOVERY_PREFIX}/sensor/wonderfree_{DEVICE_KEY}_bridge_relogins/config", {
            "name": "Bridge Relogins",
            "uniq_id": f"wonderfree_{DEVICE_KEY}_bridge_relogins",
            "state_topic": f"{SENSOR_BASE_TOPIC}/bridge_relogins",
            "device": base_dev,
        })

    def _fetch_attrs_raw(self, session: requests.Session):
        url = f"{REALTIME_ATTRS_URL}?dk={DEVICE_KEY}&pk={PRODUCT_KEY}&_t={int(time.time()*1000)}"

        if not getattr(session, "_wf_retry_configured", False):
            try:
                retry = Retry(
                    total=2, connect=2, read=2, backoff_factor=0.3,
                    status_forcelist=(502, 503, 504),
                    allowed_methods=frozenset(["GET"]), raise_on_status=False,
                )
                adapter = HTTPAdapter(max_retries=retry, pool_connections=2, pool_maxsize=2)
                session.mount("https://", adapter)
                session.mount("http://", adapter)
            except Exception:
                pass
            session._wf_retry_configured = True

        timeout = float(HTTP_TIMEOUT) if HTTP_TIMEOUT else 10.0

        if not hasattr(self, "_rt_fail_count"):
            self._rt_fail_count = 0
            self._rt_last_warn = 0.0

        for attempt in (0, 1):
            try:
                headers = {"Connection": "close"} if attempt == 1 else None
                r = session.get(url, timeout=timeout, headers=headers)
                r.raise_for_status()
                j = r.json()
                if j.get("code") == 200:
                    data = j.get("data") or {}
                    dev_data = data.get("deviceData") or {}
                    update_time = dev_data.get("updateTime")
                    if update_time and update_time > 1e12:
                        update_time = update_time // 1000
                    # La funzione self._parse_customize_tsl è in wf_sensors, chiamata da qui:
                    kv = self._parse_customize_tsl(data.get("customizeTslInfo") or [])
                    self._rt_fail_count = 0
                    return kv, update_time, dev_data
                return None, None, None
            except Exception as e:
                self._rt_fail_count += 1
                if self._rt_fail_count <= 2:
                    log.debug(f"[REALTIME] Fetch transient error ({type(e).__name__}): {e}")
                else:
                    now = time.time()
                    if now - self._rt_last_warn > 120:
                        log.warning(f"[REALTIME] Fetch error: {e}")
                        log.warning("[REALTIME] nessun dato")
                        self._rt_last_warn = now
                if attempt == 0:
                    time.sleep(0.2)
                    continue
                return None, None, None

    def _clear_retained(self):
        """Pubblica payload vuoto su tutti i topic retained noti.

        HA rimuove automaticamente un'entity quando riceve payload vuoto
        sul suo topic di discovery config (retain=true).
        Copre: state/*, JSON aggregati, debug, availability,
               discovery config sensori attivi, switch/select/number,
               bridge health, e topic legacy di versioni precedenti.
        """
        if not self.local:
            return

        def _pub(t):
            try:
                self.local.publish(t, b"", qos=0, retain=True)
            except Exception:
                pass

        # ── 1. Topic JSON aggregati e debug ──────────────────────────────
        _pub(SENSOR_JSON_TOPIC)
        _pub(SENSOR_JSON_RAW_TOPIC)
        _pub(SENSOR_DEBUG_TOPIC)

        # ── 2. Availability ──────────────────────────────────────────────
        _pub(AVAIL_TOPIC)
        _pub(f"{HA_BASE}/availability")

        # ── 3. Tutti i topic state/* attualmente noti ─────────────────────
        _state_keys = [
            # battery
            "battery_percentage", "battery_voltage", "battery_current",
            "battery_temp", "remaining_time",
            "charge_time_to_full", "charge_time_to_full_str",
            # pv / solar
            "pv_input_power", "pv_input_energy",
            # ac
            "ac_input_power", "ac_output_power",
            "ac_output_voltage", "ac_output_current",
            # dc
            "dc_input_power", "dc_output_power",
            "dc24v_voltage", "dc24v_current",
            "dc12v1_voltage", "dc12v1_current",
            "dc12v2_voltage", "dc12v2_current",
            # type-c
            "typec_1_voltage", "typec_1_current", "typec_1_power",
            "typec_2_voltage", "typec_2_current", "typec_2_power",
            # usb
            "usb_a1_voltage", "usb_a1_current", "usb_a1_power",
            "usb_a2_voltage", "usb_a2_current", "usb_a2_power",
            "usb_a3_voltage", "usb_a3_current", "usb_a3_power",
            "usb_a4_voltage", "usb_a4_current", "usb_a4_power",
            # grid
            "grid_voltage", "grid_freq",
            # totali
            "total_input_power", "total_output_power",
            # temperature
            "temp", "temp_bms", "temp_inv", "temp_mppt",
            # misc
            "signal_strength", "fault_code", "device_status", "updated_at",
            # switch/select states
            "ac_switch", "dc_switch", "screen_sleeptime_set", "screen_sleeptime_set_label",
            "grid_output", "ac_charging_limit", "beep_setting",
            "led_status", "mode_set", "output_power_set",
            # bridge health
            "bridge_uptime", "bridge_relogins",
        ]
        for k in _state_keys:
            _pub(f"{SENSOR_BASE_TOPIC}/{k}")

        # ── 4. Discovery config switch / select / number (attivi) ─────────
        for cfg_t in (
            AC_CFG_TOPIC, DC_CFG_TOPIC, LED_CFG_TOPIC, SCREEN_CFG_TOPIC,
            GRIDOUT_CFG_TOPIC, BEEP_CFG_TOPIC, SLOWCHG_CFG_TOPIC,
            MODE_CFG_TOPIC, OUTPOW_CFG_TOPIC,
        ):
            _pub(cfg_t)

        # ── 5. Discovery config sensori attivi ───────────────────────────
        _sensor_ids = [
            "battery_percentage", "remaining_time", "charge_time_to_full_str",
            "battery_voltage", "battery_current", "battery_temp",
            "ac_input_power", "ac_output_power", "ac_output_voltage", "ac_output_current",
            "pv_input_power", "pv_input_energy",
            "dc_input_power", "total_input_power", "total_output_power",
            "grid_voltage", "grid_freq",
            "dc24v_voltage", "dc24v_current",
            "dc12v1_voltage", "dc12v1_current",
            "dc12v2_voltage", "dc12v2_current",
            "typec_1_voltage", "typec_1_current", "typec_1_power",
            "typec_2_voltage", "typec_2_current", "typec_2_power",
            "usb_a1_voltage", "usb_a1_current", "usb_a1_power",
            "usb_a2_voltage", "usb_a2_current", "usb_a2_power",
            "usb_a3_voltage", "usb_a3_current", "usb_a3_power",
            "usb_a4_voltage", "usb_a4_current", "usb_a4_power",
            "temp_bms", "temp_inv", "temp_mppt",
            "signal_strength", "fault_code", "device_status", "output_power_set",
            "bridge_uptime", "bridge_relogins",
        ]
        for sid in _sensor_ids:
            uniq = f"wonderfree_{DEVICE_KEY}_{sid}"
            _pub(f"{DISCOVERY_PREFIX}/sensor/{uniq}/config")

        # ── 6. Discovery config topic legacy (vecchie versioni) ───────────
        _legacy_sensor_ids = [
            "battery_total_charged",
            "solar_panel_power_generation",
            "ac_charging_power",
            "dc_charging_power",
        ]
        for sid in _legacy_sensor_ids:
            uniq = f"wonderfree_{DEVICE_KEY}_{sid}"
            _pub(f"{DISCOVERY_PREFIX}/sensor/{uniq}/config")

        # Legacy discovery topics senza device_key (alcune versioni vecchie)
        for slug in ("wonderfree_ac", "wonderfree_dc", "wonderfree_led",
                     "wonderfree_screen", "wonderfree_mode"):
            _pub(f"{DISCOVERY_PREFIX}/switch/{slug}/config")
            _pub(f"{DISCOVERY_PREFIX}/select/{slug}/config")

        log.debug("[INIT] _clear_retained: tutti i topic retained puliti")
