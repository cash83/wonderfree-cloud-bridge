# Auto-split from original bridge.py
# Module: wf_sensors
from __future__ import annotations

from wf_config import * # noqa: F403,F401
from wf_config import OBSERVER_ONLY
import os
import json
import time
import random
import threading
import hashlib
from typing import Any, Dict, Optional

import requests

from wf_crypto import normalize_bearer


def _num(v):
    """Convert various numeric-ish values to int/float safely.
    Returns None if conversion fails or input is None/empty.
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        s = str(v).strip()
        if s == "" or s.lower() in ("none", "null", "nan"):
            return None
        # handle commas as decimal separators
        s = s.replace(",", ".")
        f = float(s)
        # if it's an integer-like float, return int to keep JSON compact
        return int(f) if f.is_integer() else f
    except Exception:
        return None

# Opzione: pulizia retained all'avvio (se non definita in wf_config)
try:
    CLEAR_RETAINED  # type: ignore[name-defined]
except Exception:
    CLEAR_RETAINED = os.getenv("CLEAR_RETAINED", "0").strip() in ("1", "true", "yes", "on")


def attach(Bridge):
    def _schedule_quick_refetch(self, reason: str = "bus"):
        """Fetch stato HTTP quasi subito quando il cloud manda BUS realtime.

        I frame /bus arrivano velocemente anche quando il cambio parte dall'app
        ufficiale, ma non sono JSON facili da pubblicare direttamente. Usiamoli
        come trigger per leggere subito gli attributi realtime, con throttle per
        evitare raffiche HTTP durante i burst.
        """
        try:
            now = time.time()
            if now < float(getattr(self, "_quick_refetch_next", 0.0) or 0.0):
                return
            if getattr(self, "_quick_refetch_running", False):
                return
            self._quick_refetch_next = now + 10.0
            self._quick_refetch_running = True

            def _worker():
                try:
                    time.sleep(0.8)
                    sess = requests.Session()
                    for k, v in APP_HEADERS.items():
                        sess.headers[k] = v
                    tok = self.token_mgr.ensure()
                    sess.headers["Authorization"] = normalize_bearer(tok)

                    kv, dev_update, dev_data = self._fetch_attrs_raw(sess)
                    if kv and self.local:
                        st = self._normalize_state(kv, dev_update)
                        try:
                            if dev_data:
                                sig = _num(dev_data.get("signalStrength"))
                                if sig is not None:
                                    st["signal_strength"] = sig
                        except Exception:
                            pass
                        self._publish_state(st, kv, debug=True)
                        self._reconcile_switch_states(st)
                        log.debug(f"[QUICK-REFETCH] reason={reason} published")
                    else:
                        log.debug(f"[QUICK-REFETCH] reason={reason} no data")
                except Exception as e:
                    log.debug(f"[QUICK-REFETCH] reason={reason} failed: {e}")
                finally:
                    self._quick_refetch_running = False

            threading.Thread(target=_worker, daemon=True).start()
        except Exception:
            self._quick_refetch_running = False

    Bridge._schedule_quick_refetch = _schedule_quick_refetch

    def start(self):
        self.token_mgr.ensure()
        self._connect_local()

        if CLEAR_RETAINED and self.local:
            log.debug('[INIT] Clearing retained under state/* and JSON topics')
            self._clear_retained()
            # Dopo la pulizia dei retained, ripubblica subito la discovery.
            # Senza questo passaggio Home Assistant puo' rimuovere alcune entita'
            # fino al successivo reconnect MQTT.
            try:
                self._on_local_connect(self.local, None, None, 0)
            except Exception as e:
                log.warning(f"[INIT] republish discovery after clear_retained failed: {e}")

        self._connect_remote()

        threading.Thread(target=self._poll_loop, daemon=True).start()
        threading.Thread(target=self._watchdog_loop, daemon=True).start()

        log.debug(
            f"FINAL (Verbose + Beep) bridge started. "
            f"SEND_STRATEGY={SEND_STRATEGY.upper()} DEDUP_MS={DEDUP_MS} STALE_SEC={STALE_SEC}"
        )
    Bridge.start = start

    def _get_first(self, kv: dict, *candidates, default=None):
        for c in candidates:
            if c in kv and kv[c] is not None:
                return kv[c]
        return default
    Bridge._get_first = _get_first

    def _bus_chan(self) -> int:
        """Ritorna il 'channel' word osservato nei frame BUS.

        Dai log:
          - topic q/1/... usa 0x0013
          - topic q/2/... usa 0x0014
        """
        try:
            t = BUS_TOPIC  # noqa: F405
            return 0x0013 if str(t).startswith("q/1/") else 0x0014
        except Exception:
            return 0x0014

    Bridge._bus_chan = _bus_chan

    def _publish_bus_frame(self, frame: bytes):
        """Pubblica un frame BUS sul topic principale (q/1).

        Dalla Frida, i PUBLISH dell'app vanno SOLO su q/1/.../bus.
        NON inviamo su q/2 per evitare che il device riceva ogni comando due volte,
        il che può causare il freeze del firmware.
        """
        try:
            if getattr(self, "remote", None) is None:
                return

            main_topic = BUS_TOPIC  # noqa: F405
            self.remote.publish(main_topic, payload=frame, qos=0, retain=False)

        except Exception as e:
            log.warning(f"[TX] publish BUS frame failed: {e}")

    Bridge._publish_bus_frame = _publish_bus_frame

    def _send_bus_refresh(self, mode: int = 0x02, throttle_s: float = 30.0):
        """Invia refresh BUS 0x009A *identico a Frida/app*.

        Frida:
          aaaa0009 b3 0004 0013 009a 0002
        Struttura:
          AA AA | 00 09 | SEQ(1) | PKT(2) | CHAN(2) | 00 9A | 00 MODE

        In OBSERVER_ONLY mode non invia nulla.
        """
        if OBSERVER_ONLY:
            return
        try:
            if not hasattr(self, "_next_bus_refresh"):
                self._next_bus_refresh = 0.0

            now = time.time()
            if now < self._next_bus_refresh:
                return

            # SEQ: 1 byte incrementale
            seq = getattr(self, "_bus_seq", None)
            if seq is None:
                seq = 0xB3  # app-like start
            else:
                seq = (seq + 1) & 0xFF
            self._bus_seq = seq

            # PKT: 2 byte incrementali (Frida: 0004, 0005...)
            pkt = getattr(self, "_bus_pkt", None)
            if pkt is None:
                pkt = 0x0004  # app-like start
            else:
                pkt = (pkt + 1) & 0xFFFF
            self._bus_pkt = pkt

            chan = self._bus_chan()

            frame = (
                b"\xAA\xAA" +
                b"\x00\x09" +
                bytes([seq]) +
                pkt.to_bytes(2, "big") +
                chan.to_bytes(2, "big") +
                b"\x00\x9A" +
                bytes([0x00, mode & 0xFF])
            )

            self._publish_bus_frame(frame)

            log.debug(f"[TX] BUS refresh sent chan=0x{chan:04x} mode=0x{mode:02x} hex={frame.hex()}")
            self._next_bus_refresh = now + float(throttle_s)
        except Exception as e:
            log.warning(f"[TX] BUS refresh failed: {e}")

    Bridge._send_bus_refresh = _send_bus_refresh


    def _send_bus_mask_0011(self, ids, throttle_s: float = 3600.0):
        """Invia frame BUS 0x0011 (mask/list) come da Frida.

        Frida esempio (len=0x0041):
          AA AA 00 41 49 0003 0011 <lista 2-byte ids...>
        Qui:
          - SEQ: 1 byte
          - PKT: 2 byte
          - CMD: 0x0011
          - PAYLOAD: ids (ognuno 2 byte big endian)
        """
        try:
            if not hasattr(self, "_next_bus_mask"):
                self._next_bus_mask = 0.0

            now = time.time()
            if now < self._next_bus_mask:
                return

            seq = getattr(self, "_mask_seq", None)
            if seq is None:
                seq = 0x49  # app-like start
            else:
                seq = (seq + 1) & 0xFF
            self._mask_seq = seq

            pkt = getattr(self, "_mask_pkt", None)
            if pkt is None:
                pkt = 0x0003  # app-like start
            else:
                pkt = (pkt + 3) & 0xFFFF
            self._mask_pkt = pkt

            ids_bytes = b"".join(int(x).to_bytes(2, "big") for x in (ids or []))
            # length = 1(seq) + 2(pkt) + 2(cmd) + payload_len
            ln = 1 + 2 + 2 + len(ids_bytes)

            frame = b"\xAA\xAA" + ln.to_bytes(2, "big") + bytes([seq]) + pkt.to_bytes(2, "big") + b"\x00\x11" + ids_bytes

            self._publish_bus_frame(frame)

            log.debug(f"[TX] BUS mask 0x0011 sent ids={len(ids or [])} len=0x{ln:04x} hex={frame.hex()}")
            self._next_bus_mask = now + float(throttle_s)
        except Exception as e:
            log.warning(f"[TX] BUS mask 0x0011 failed: {e}")

    Bridge._send_bus_mask_0011 = _send_bus_mask_0011

    def _fetch_cloud_device_status(self, sess) -> Optional[bool]:
        """Read online/offline from userDeviceList, same cloud view shown by the app."""
        now = time.time()
        last_check = float(getattr(self, "_last_cloud_status_check", 0.0) or 0.0)
        poll_sec = 60.0
        if (now - last_check) < poll_sec:
            return getattr(self, "_cloud_online_status", None)

        self._last_cloud_status_check = now

        try:
            r = sess.get(
                BASE_URL.rstrip("/") + "/v2/binding/enduserapi/userDeviceList",
                params={"pageSize": 20, "isAssociated": 1},
                timeout=max(float(HTTP_TIMEOUT), 8.0),
            )
            j = r.json()
            if j.get("code") != 200:
                raise RuntimeError(f"code={j.get('code')} msg={j.get('msg')!r}")

            data = j.get("data") or {}
            items = data.get("list") if isinstance(data, dict) else data
            if not isinstance(items, list):
                return getattr(self, "_cloud_online_status", None)

            for item in items:
                if str(item.get("deviceKey") or "").strip() != str(DEVICE_KEY):
                    continue

                raw_online = item.get("onlineStatus")
                online = None
                if raw_online is not None:
                    try:
                        online = int(raw_online) == 1
                    except Exception:
                        online = str(raw_online).strip() in ("1", "true", "True", "online", "ONLINE", "在线")
                if online is None:
                    dev_status = str(item.get("deviceStatus") or "").strip()
                    if dev_status:
                        online = dev_status not in ("离线", "offline", "OFFLINE", "0")

                prev = getattr(self, "_cloud_online_status", None)
                self._cloud_online_status = online
                self._device_status_last_detail = {
                    "deviceStatus": item.get("deviceStatus"),
                    "onlineStatus": item.get("onlineStatus"),
                    "lastConnTime": item.get("lastConnTime"),
                    "lastOfflineTime": item.get("lastOfflineTime"),
                }
                if prev is not online:
                    log.info(
                        f"[CLOUD-STATUS] online={online} "
                        f"deviceStatus={item.get('deviceStatus')} "
                        f"lastConnTime={item.get('lastConnTime')} "
                        f"lastOfflineTime={item.get('lastOfflineTime')}"
                    )
                return online

        except Exception as e:
            log.debug(f"[CLOUD-STATUS] fetch failed: {e}")

        return getattr(self, "_cloud_online_status", None)
    Bridge._fetch_cloud_device_status = _fetch_cloud_device_status

    def _maybe_send_periodic_mask(self, throttle_s: Optional[float] = None):
        """Invia il frame mask 0x0011 periodicamente per imitare meglio l'app.

        L'app tende a reinviare la mask quando la sessione è attiva; qui lo facciamo
        in modo molto conservativo per evitare raffiche inutili.
        In OBSERVER_ONLY mode non invia nulla.
        """
        try:
            if OBSERVER_ONLY:
                return False
            if DISABLE_STARTUP_MASK:
                return False
            if throttle_s is None:
                throttle_s = float(MASK_REFRESH_INTERVAL)
            self._send_bus_mask_0011(
                [0x001e,0x0027,0x0021,0x001c,0x0020,0x0026,0x0022,0x001f,0x000b,0x001d,0x0010,0x0011,0x000d,0x002f,0x000a,0x0005,0x0008,0x0009,0x0003,0x0006,0x0002,0x001b,0x0010,0x0001,0x000f,0x000c,0x0011,0x000e,0x0012,0x0004],
                throttle_s=float(throttle_s),
            )
            return True
        except Exception:
            return False
    Bridge._maybe_send_periodic_mask = _maybe_send_periodic_mask

    def _poll_loop(self):
        sess = requests.Session()
        for k, v in APP_HEADERS.items():
            sess.headers[k] = v

        last_digest = None
        consecutive_errors = 0
        max_errors = 3

        while not self.stop.is_set():
            now = time.time()
            if now - self.start_t <= STARTUP_BURST_SECONDS:
                period = STARTUP_BURST_PERIOD
            else:
                period = self.adaptive_period

            did_refresh = False

            # throttle per warning/errore HTTP, per non "spammare" i log
            if not hasattr(self, "_rt_last_warn_no_data"):
                self._rt_last_warn_no_data = 0.0
            if not hasattr(self, "_rt_last_warn_http"):
                self._rt_last_warn_http = 0.0

            try:
                # Aggiorna token se necessario
                tok = self.token_mgr.ensure()
                sess.headers["Authorization"] = normalize_bearer(tok)

                # Cloud online/offline view, same source used by the app device list.
                cloud_online = self._fetch_cloud_device_status(sess)
                if cloud_online is False:
                    self._pending_offline_since = now
                    self._device_force_offline = True
                    self._onl_declared_offline = True
                    if self.local:
                        self.local.publish(AVAIL_TOPIC, AVAIL_PAYLOAD_OFF, qos=0, retain=True)
                    # Se il cloud lo vede offline, non stimolare il device e non leggere HTTP cached.
                    # NOTA: nessun time.sleep qui — ci pensa il finally per evitare doppio sleep.
                    continue

                # App-like BUS mask (0x0011) + refresh (0x009A0002) per stimolare il BURST.
                # IMPORTANTISSIMO: niente "mitragliate".
                if (cloud_online is not False) and (not getattr(self, "_startup_bus_done", False)) and (time.time() - self.start_t < 15):
                    self._startup_bus_done = True

                    # 1) mask (opzionale)
                    if not DISABLE_STARTUP_MASK:
                        self._maybe_send_periodic_mask(throttle_s=3600.0)

                    # 2) startup refresh: pochi, con jitter e gap minimo
                    cnt = max(0, int(STARTUP_REFRESH_COUNT))
                    if cnt:
                        for i in range(cnt):
                            self._send_bus_refresh(mode=0x02, throttle_s=0.0)
                            did_refresh = True
                            if i < cnt - 1:
                                jitter = max(0, int(STARTUP_REFRESH_JITTER_MS)) / 1000.0
                                time.sleep(max(float(REFRESH_MIN_GAP), 1.0) + random.uniform(0.0, jitter))

                    self._next_periodic_refresh = time.time() + random.uniform(float(REFRESH_MIN), float(REFRESH_MAX))

                else:
                    # refresh periodico BUS 0x009A mode=0x02
                    now = time.time()
                    if cloud_online is False:
                        can_send = False
                        burst_until = getattr(self, "_burst_until", 0.0)
                    else:
                        burst_until = getattr(self, "_burst_until", 0.0)

                        if not hasattr(self, "_next_refresh_02"):
                            self._next_refresh_02 = now + random.uniform(float(REFRESH_MIN), float(REFRESH_MAX))

                        if not hasattr(self, "_next_any_refresh"):
                            self._next_any_refresh = 0.0

                        can_send = (now >= float(burst_until)) and (now >= float(self._next_any_refresh))

                        if can_send and now >= float(getattr(self, "_next_refresh_02", now + 1e9)):
                            self._maybe_send_periodic_mask()
                            self._send_bus_refresh(mode=0x02, throttle_s=0.0)
                            did_refresh = True
                            self._next_refresh_02 = now + random.uniform(float(REFRESH_MIN), float(REFRESH_MAX))
                            self._next_any_refresh = now + max(float(REFRESH_MIN_GAP), 1.0)

                if did_refresh:
                    delay_s = max(0.0, float(REFRESH_HTTP_DELAY_MS) / 1000.0)
                    if delay_s > 0:
                        time.sleep(delay_s)


                # Salta HTTP solo se: ACK fresco (<30s) E last_publish_ts è recente (<STALE_SEC/2).
                # Se last_publish_ts è vecchio (device potenzialmente offline) HTTP deve girare
                # per ottenere il vero updateTime e far scattare il watchdog correttamente.
                _ack_age   = time.time() - getattr(self, '_last_ack_ts', 0)
                _ts_age    = time.time() - self.last_publish_ts if self.last_publish_ts > 0 else 9999
                if not did_refresh and _ack_age < 30 and _ts_age < (STALE_SEC / 2):
                    # NOTA: nessun time.sleep qui — ci pensa il finally per evitare doppio sleep.
                    continue

                # Fetch dati real-time (HTTP)
                kv, dev_update, dev_data = self._fetch_attrs_raw(sess)
                if did_refresh and QUICK_REFETCH_AFTER_REFRESH and not kv:
                    time.sleep(max(0.8, float(REFRESH_HTTP_DELAY_MS) / 1000.0))
                    kv, dev_update, dev_data = self._fetch_attrs_raw(sess)

                if kv and self.local:
                    st = self._normalize_state(kv, dev_update)

                    # Enrichment da deviceData (stessa risposta HTTP)
                    try:
                        if dev_data:
                            # Ignoriamo dev_data.get("battery") perché è buggato e fisso a 100
                            sig = _num(dev_data.get("signalStrength"))
                            if sig is not None:
                                st["signal_strength"] = sig
                    except Exception:
                        pass

                    self._publish_state(st, kv, debug=True)
                    self._reconcile_switch_states(st)

                    # Logging ridotto per non intasare
                    age = int(time.time()) - int(st.get("updated_at", 0))

                    # Se i dati diventano vecchi, accelera il polling e manda un refresh BUS.
                    # NON mandiamo la mask (0x0011) qui: è un frame pesante e non serve
                    # in fase di stale; verrebbe inviata troppo spesso causando freeze del device.
                    if age > STALE_SEC:
                        self.adaptive_period = max(POLL_MIN, 2)
                        now2 = time.time()
                        if now2 >= float(getattr(self, "_next_any_refresh", 0.0)):
                            self._send_bus_refresh(mode=0x02, throttle_s=0.0)
                            self._next_any_refresh = now2 + max(float(REFRESH_MIN_GAP), 1.0)
                            self._next_refresh_02 = min(float(getattr(self, "_next_refresh_02", now2)), now2 + max(float(REFRESH_MIN_GAP), 1.0))

                    if not MUTE_POLL or age > 30:
                        log.debug(
                            f"[REALTIME] battery={st.get('battery_percentage')}% "
                            f"in={st.get('total_input_power')}W "
                            f"out={st.get('total_output_power')}W age={age}s"
                        )

                    # Adaptive polling basato sui cambiamenti
                    st_digest = dict(st)
                    st_digest.pop("updated_at", None)
                    digest = hashlib.md5(json.dumps(st_digest, sort_keys=True).encode()).hexdigest()
                    changed = (digest != last_digest) or (
                        dev_update is not None and dev_update != getattr(self, "_last_dev_update", None)
                    )
                    self._last_dev_update = dev_update
                    last_digest = digest

                    if changed:
                        self.adaptive_period = max(POLL_MIN, 2)
                        consecutive_errors = 0
                    else:
                        self.adaptive_period = min(POLL_MAX, self.adaptive_period + 1)

                else:
                    # Nessun dato (HTTP 200 ma body "vuoto"/non parseabile): capita. Non è un errore "grave".
                    consecutive_errors += 1
                    tnow = time.time()
                    if (tnow - float(self._rt_last_warn_no_data)) >= 300.0:  # max 1 warning ogni 5 min
                        log.warning("[REALTIME] nessun dato")
                        self._rt_last_warn_no_data = tnow

            except Exception as e:
                consecutive_errors += 1

                # alcuni server chiudono la connessione senza risposta: ricrea la Session per ripulire lo stato
                if isinstance(e, requests.exceptions.RequestException) or "RemoteDisconnected" in repr(e):
                    try:
                        sess.close()
                    except Exception:
                        pass
                    sess = requests.Session()
                    for k, v in APP_HEADERS.items():
                        sess.headers[k] = v

                tnow = time.time()
                if (tnow - float(self._rt_last_warn_http)) >= 300.0:  # max 1 warning ogni 5 min
                    log.warning(f"[REALTIME] Fetch error: {e!r}")
                    self._rt_last_warn_http = tnow
                else:
                    log.debug(f"[REALTIME] Fetch error (throttled): {e!r}")

            finally:
                # backoff leggero se continuiamo a fallire
                if consecutive_errors >= max_errors:
                    time.sleep(min(5.0, float(period)))
                else:
                    time.sleep(float(period))
    Bridge._poll_loop = _poll_loop

    def _parse_customize_tsl(self, tsl_list):
        kv = {}
        for item in tsl_list or []:
            code = item.get("resourceCode")
            val = item.get("resourceValce")
            if not code:
                continue

            if isinstance(val, str):
                v = val.strip()
                if v.startswith("{") and v.endswith("}"):
                    try:
                        kv[code] = json.loads(v)
                        continue
                    except Exception:
                        pass
            kv[code] = val

        def _int(n):
            if n in kv:
                try:
                    kv[n] = int(float(kv[n]))
                except Exception:
                    pass

        def _bool(n):
            if n in kv:
                v = str(kv[n]).strip().lower()
                if v in ("true", "1", "on", "yes"):
                    kv[n] = True
                elif v in ("false", "0", "off", "no"):
                    kv[n] = False
                # se arriva roba strana, lascio com'è

        for n in ["device_status","signal_strength_set","mode","led_status_set","output_power_set",
                  "relay_mode","light_mode","electricity_data"]:
            _int(n)
        for n in ["ac_switch","dc_switch","grid_power_switch_set","ac_charging_limit_set","beep_setting_set",
                  "switch","child_lock","over_charge"]:
            _bool(n)

        for key in ("battery_data","ac_data","dc_data","pv_data","grid_data","temp_data_no"):
            if isinstance(kv.get(key), dict):
                for k in list(kv[key].keys()):
                    kv[key][k] = _num(kv[key][k])

        bd = kv.get("battery_data") or {}
        if isinstance(bd, dict) and bd.get("soc") is not None:
            kv["battery_percentage"] = _num(bd.get("soc"))
        return kv
    Bridge._parse_customize_tsl = _parse_customize_tsl

    def _publish_state(self, st: Dict[str, Any], raw: Dict[str, Any], debug: bool = True):
        changed_only = PUBLISH_ONLY_CHANGED
        try:
            # Il topic JSON aggregato viene pubblicato sempre (è usato da tutti i sensori via value_template)
            self.local.publish(SENSOR_JSON_TOPIC, json.dumps(st).encode(), qos=0, retain=True)
            if isinstance(raw, dict):
                self.local.publish(SENSOR_JSON_RAW_TOPIC, json.dumps(raw).encode(), qos=0, retain=True)

            for k, v in st.items():
                t = f"{SENSOR_BASE_TOPIC}/{k}"
                if v is None:
                    payload = b""
                elif isinstance(v, (dict, list)):
                    payload = json.dumps(v).encode()
                else:
                    payload = str(v).encode()

                # Se publish_only_changed=true, salta i valori identici all'ultima pubblicazione
                if changed_only and self._last_published.get(t) == payload:
                    continue
                self._last_published[t] = payload
                self.local.publish(t, payload, qos=0, retain=True)

            # bridge health
            uptime_min = int((time.time()-self.start_t)/60)
            self.local.publish(f"{SENSOR_BASE_TOPIC}/bridge_uptime", str(uptime_min).encode(), qos=0, retain=True)
            self.local.publish(f"{SENSOR_BASE_TOPIC}/bridge_relogins", b"0", qos=0, retain=True)
            ts_val = int(st.get("updated_at") or 0)
            # Non aggiornare last_publish_ts se onl_ ha dichiarato il device offline:
            # evita che dati HTTP cached (ancora freschi per qualche secondo) riportino online.
            if ts_val > 0 and not getattr(self, '_device_force_offline', False):
                self.last_publish_ts = ts_val

            if debug:
                try:
                    snap = {k: st.get(k) for k in [
                        "updated_at","battery_percentage","temp","total_input_power","total_output_power",
                        "ac_input_power","device_status","remaining_time","mode_set","led_status","output_power_set"
                    ]}
                    self.local.publish(SENSOR_DEBUG_TOPIC, json.dumps(snap).encode(), qos=0, retain=True)
                except Exception:
                    pass

        except Exception:
            pass
    Bridge._publish_state = _publish_state

    def _normalize_state(self, kv: Dict[str, Any], dev_update) -> Dict[str, Any]:
        st: Dict[str, Any] = {}

        # base
        _ds_raw = kv.get("device_status")
        _ds_int = _num(_ds_raw)
        st["device_status"] = DEVICE_STATUS_LABEL.get(int(_ds_int), str(_ds_raw)) if _ds_int is not None else _ds_raw  # noqa: F405

        # battery data
        bd = kv.get("battery_data") or {}
        st["battery_percentage"] = kv.get("battery_percentage", _num(bd.get("soc")))
        st["battery_voltage"]    = _num(bd.get("battery_total_voltage"))
        battery_power            = _num(bd.get("battery_total_power"))
        battery_voltage          = st["battery_voltage"]

        if battery_power is not None and battery_voltage and battery_voltage > 10:
            calc_current = battery_power / battery_voltage
            st["battery_current"] = round(calc_current, 2) if -100 <= calc_current <= 100 else 0.0
        else:
            st["battery_current"] = 0.0

        st["battery_temp"] = _num(bd.get("battery_temp"))

        # remaining_time in minuti -> ore
        # 65535 (0xFFFF) = sentinella "non disponibile" (es. batteria piena, bypass mode)
        raw_remaining = _num(bd.get("remaining_time"))
        if raw_remaining is not None and raw_remaining < 65535:
            st["remaining_time"] = round(raw_remaining / 60, 2)
        else:
            st["remaining_time"] = 0

        # pv
        pd = kv.get("pv_data") or {}
        st["pv_input_power"]  = _num(pd.get("pv_total_power"))
        st["pv_input_energy"] = _num(kv.get("solar_panel_power_generation"))

        # grid / ac power split
        gd = kv.get("grid_data") or {}
        grid_p = _num(gd.get("grid_b_power"))
        st["grid_voltage"] = _num(gd.get("grid_voltage"))
        st["grid_freq"]    = _num(gd.get("grid_frequency"))

        st["ac_input_power"]  = 0
        st["ac_output_power"] = 0
        if grid_p is not None:
            if grid_p >= 0:
                st["ac_output_power"] = grid_p
            else:
                st["ac_input_power"] = abs(grid_p)

        # ac block
        ad = kv.get("ac_data") or {}
        st["ac_output_voltage"] = _num(ad.get("ac_voltage"))
        ac_p = _num(ad.get("ac_power"))
        if st.get("ac_output_power") == 0 and ac_p:
            st["ac_output_power"] = ac_p
        v = st.get("ac_output_voltage") or 0
        p = st.get("ac_output_power") or 0
        st["ac_output_current"] = round(p / v, 2) if v else 0

        # --- DC block ---
        dd = kv.get("dc_data") or {}

        # DC 24V
        dc24v_voltage = _num(dd.get("dc_24V_voltage"))
        dc24v_power = _num(dd.get("dc_24V_power"))
        st["dc24v_voltage"] = dc24v_voltage
        st["dc24v_current"] = round(dc24v_power / dc24v_voltage, 2) if dc24v_voltage else 0

        # DC 12V (duplicato su due canali)
        dc12v_voltage = _num(dd.get("dc_12V_voltage"))
        dc12v_power = _num(dd.get("dc_12V_power"))
        dc12v_current = round(dc12v_power / dc12v_voltage, 2) if dc12v_voltage else 0

        st["dc12v1_voltage"] = dc12v_voltage
        st["dc12v2_voltage"] = dc12v_voltage
        st["dc12v1_current"] = dc12v_current
        st["dc12v2_current"] = dc12v_current

        # Totale potenza DC in uscita
        st["dc_output_power"] = _num(dd.get("dc_total_power"))

        # Campo opzionale DC input
        st["dc_input_power"] = _num(dd.get("dc_input_power", 0))

        # type-c + usb from dc_data
        for i in (1, 2):
            st[f"typec_1_voltage" if i==1 else "typec_2_voltage"] = _num(dd.get(f"typec_{i}_voltage"))
            st[f"typec_1_power"   if i==1 else "typec_2_power"]   = _num(dd.get(f"typec_{i}_power"))
            vv = st.get("typec_1_voltage" if i==1 else "typec_2_voltage") or 0
            pp = st.get("typec_1_power"   if i==1 else "typec_2_power") or 0
            st[f"typec_1_current" if i==1 else "typec_2_current"] = round(pp / vv, 2) if vv else 0

        for i in range(1,5):
            st[f"usb_a{i}_voltage"] = _num(dd.get(f"usb_{i}_voltage"))
            st[f"usb_a{i}_power"]   = _num(dd.get(f"usb_{i}_power"))
            vv = st.get(f"usb_a{i}_voltage") or 0
            pp = st.get(f"usb_a{i}_power") or 0
            st[f"usb_a{i}_current"] = round(pp / vv, 2) if vv else 0

        # temps
        td = kv.get("temp_data_no") or {}
        st["temp_bms"]  = _num(td.get("bms_mos_temp"))
        st["temp_inv"]  = _num(td.get("inv_temp_max"))
        st["temp_mppt"] = _num(td.get("mppt_temp_max"))
        valid = [t for t in (st.get("temp_bms"), st.get("temp_inv"), st.get("temp_mppt")) if t is not None]
        if valid:
            st["temp"] = round(sum(valid) / len(valid), 1)

        # misc
        st["signal_strength"] = _num(kv.get("signal_strength_set"))
        st["fault_code"] = _num(kv.get("fault_code"))

        # total input/output power calculation
        pv_in = st.get("pv_input_power") or 0
        ac_in = st.get("ac_input_power") or 0
        st["total_input_power"] = round(pv_in + ac_in, 2)

        ac_out = st.get("ac_output_power") or 0
        dc24v_p = (st.get("dc24v_voltage") or 0) * (st.get("dc24v_current") or 0)
        dc12v1_p = (st.get("dc12v1_voltage") or 0) * (st.get("dc12v1_current") or 0)
        dc12v2_p = (st.get("dc12v2_voltage") or 0) * (st.get("dc12v2_current") or 0)
        typec_1_p = st.get("typec_1_power") or 0
        typec_2_p = st.get("typec_2_power") or 0
        usb_sum = sum(st.get(f"usb_a{i}_power") or 0 for i in range(1,5))
        st["total_output_power"] = round(ac_out + dc24v_p + dc12v1_p + dc12v2_p + typec_1_p + typec_2_p + usb_sum, 2)

        # --- Calcolo tempo stimato di carica fino a 100% ---
        # (spostato qui: total_input/output_power sono ora calcolati)
        soc = st.get("battery_percentage") or 0
        total_in = st.get("total_input_power") or 0
        total_out = st.get("total_output_power") or 0
        net_charge = total_in - total_out
        batt_current = st.get("battery_current") or 0
        batt_voltage = st.get("battery_voltage") or 0
        BATTERY_CAPACITY_WH = 2048
        is_charging = (net_charge > 10) or (batt_current > 0.5)
        if is_charging and soc < 100:
            remaining_wh = BATTERY_CAPACITY_WH * (100 - soc) / 100.0
            effective_power = net_charge if net_charge > 0 else (batt_voltage * batt_current)
            hours = remaining_wh / max(effective_power, 1)
            st["charge_time_to_full"] = round(hours, 2)
            h = int(hours)
            m = int(round((hours - h) * 60))
            st["charge_time_to_full_str"] = f"{h}h {m:02d}m" if h > 0 else f"{m}m"
        else:
            st["charge_time_to_full"] = 0
            st["charge_time_to_full_str"] = "0m"

        # debug components
        st["_debug_power_components"] = {
            "input": {"pv": pv_in, "ac": ac_in},
            "output": {
                "ac": ac_out,
                "dc24v": dc24v_p,
                "dc12v1": dc12v1_p,
                "dc12v2": dc12v2_p,
                "typec1": typec_1_p,
                "typec2": typec_2_p,
                "usb_sum": usb_sum
            }
        }
        # switch states
        st["ac_switch"] = kv.get("ac_switch")
        st["dc_switch"] = kv.get("dc_switch")
        st["screen_sleeptime_set"] = kv.get("screen_sleeptime_set")
        _screen_timeout_raw = kv.get("screen_sleeptime_set")
        try:
            _screen_timeout_raw = int(float(_screen_timeout_raw)) if _screen_timeout_raw is not None else None
        except Exception:
            pass
        st["screen_sleeptime_set_label"] = SCREEN_TIMEOUT_LABEL_BY_VAL.get(_screen_timeout_raw)
        st["ac_charging_limit"] = kv.get("ac_charging_limit_set")
        st["beep_setting"] = kv.get("beep_setting_set")
        st["led_status"] = kv.get("led_status_set", kv.get("led_status"))

        # mode select
        mv = kv.get("mode")
        if isinstance(mv,(int,float)) or (isinstance(mv,str) and mv.isdigit()):
            st["mode_set"] = MODE_LABEL_BY_VAL.get(int(mv), mv)
        else:
            st["mode_set"] = mv

        # grid output: segue SEMPRE il valore cloud se presente;
        # fallback solo se il cloud non lo manda (così puoi tenere Micro-Inverter ma spegnere Grid, come nell'app).
        cloud_grid = kv.get("grid_power_switch_set")
        if cloud_grid is not None:
            st["grid_output"] = cloud_grid
        else:
            m = str(st.get("mode_set") or "").strip()
            if m == "Micro-Inverter":
                st["grid_output"] = True
            elif m == "PPS":
                st["grid_output"] = False
            else:
                st["grid_output"] = None

        # slider current value
        st["output_power_set"] = kv.get("output_power_set")

        st["updated_at"] = int(dev_update) if dev_update else 0
        st["_ts"] = int(time.time())  # cambia sempre → forza HA ad aggiornare il timestamp "X secondi fa"
        return st

    Bridge._normalize_state = _normalize_state


    def _reconcile_switch_states(self, st: Dict[str, Any]):
        """Pubblica SEMPRE gli stati reali del cloud sui topic HA.

        - Così Home Assistant segue l'app (e viceversa).
        - La logica "pending" serve solo a:
          1) evitare flicker immediato dopo un comando HA
          2) chiudere il pending quando il cloud conferma.
        """
        now = time.time()

        def _as_bool(v):
            if v is None:
                return None
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return v != 0
            s = str(v).strip().lower()
            if s in ("1", "true", "on", "yes"):
                return True
            if s in ("0", "false", "off", "no"):
                return False
            return None

        def maybe_pub_bool(key: str, state_topic: str, cloud_val):
            pend = self.pending.get(key)
            cloud_b = _as_bool(cloud_val)

            # 1) pubblica SEMPRE lo stato reale del cloud se lo conosci
            if cloud_b is not None and self.local:
                self.local.publish(state_topic, b"ON" if cloud_b else b"OFF", qos=0, retain=True)

            # 2) se il cloud conferma, chiudi il pending
            if pend and cloud_b is not None:
                desired_b = _as_bool(pend.get("desired"))
                if desired_b is not None and cloud_b == desired_b:
                    self.pending.pop(key, None)

            # 3) pulizia pending scaduto
            if pend and now >= pend["until"]:
                self.pending.pop(key, None)

        maybe_pub_bool("ac_switch", AC_STATE_TOPIC, st.get("ac_switch"))
        maybe_pub_bool("dc_switch", DC_STATE_TOPIC, st.get("dc_switch"))
        maybe_pub_bool("grid_output", GRIDOUT_STATE_TOPIC, st.get("grid_output"))
        maybe_pub_bool("ac_charging_limit", SLOWCHG_STATE_TOPIC, st.get("ac_charging_limit"))
        maybe_pub_bool("beep_setting", BEEP_STATE_TOPIC, st.get("beep_setting"))


        led_val = st.get("led_status")
        led_on = None if led_val is None else (str(led_val).strip() not in ("0", "off", "OFF"))
        maybe_pub_bool("led_status", LED_STATE_TOPIC, led_on)
        def maybe_pub_sel(key: str, state_topic: str, cloud_txt: Optional[str]):
            pend = self.pending.get(key)

            # PUBBLICA SEMPRE lo stato reale del cloud se lo conosci
            if cloud_txt is not None and self.local:
                self.local.publish(state_topic, str(cloud_txt).encode(), qos=0, retain=True)

            # chiudi pending se il cloud conferma
            if pend and cloud_txt is not None:
                if str(cloud_txt) == str(pend.get("desired")):
                    self.pending.pop(key, None)

            # pulizia pending scaduto
            if pend and now >= pend["until"]:
                self.pending.pop(key, None)

        maybe_pub_sel("mode_set", MODE_STATE_TOPIC, st.get("mode_set"))
        maybe_pub_sel("screen_sleeptime_set", SCREEN_STATE_TOPIC, st.get("screen_sleeptime_set_label"))
        # output_power_set already published via OUTPOW_STATE_TOPIC when command sent

    # ---- Connectors ----
    Bridge._reconcile_switch_states = _reconcile_switch_states

    def _watchdog_loop(self):
        while not self.stop.is_set():
            try:
                now = int(time.time())
                last_ts    = self.last_publish_ts  # da HTTP (updateTime REALE del device)
                bridge_age = now - int(self.start_t)
                # USA SOLO last_publish_ts: aggiornato solo via HTTP con il vero updateTime.
                # _last_ack_ts escluso: il cloud manda ack_ anche col device SPENTO
                # (dati cached) → includerlo impedisce il corretto rilevamento offline.
                # HTTP gira ogni max 30s → con STALE_SEC=300s non ci sono falsi offline.
                pending_offline_since = float(getattr(self, "_pending_offline_since", 0.0) or 0.0)
                offline_debounce_sec = 45

                debounced_offline = (
                    pending_offline_since > 0 and
                    (time.time() - pending_offline_since) >= offline_debounce_sec
                )

                cloud_status = getattr(self, "_cloud_online_status", None)
                cloud_age = time.time() - float(getattr(self, "_last_cloud_status_check", 0.0) or 0.0)
                cloud_fresh = bool(
                    cloud_status is not None and
                    cloud_age <= 180.0
                )

                if cloud_fresh:
                    is_online = bool(cloud_status)
                    self._device_force_offline = not is_online
                    if self.local:
                        self.local.publish(
                            AVAIL_TOPIC,
                            AVAIL_PAYLOAD_ON if is_online else AVAIL_PAYLOAD_OFF,
                            qos=0,
                            retain=True,
                        )
                else:
                    is_stale = debounced_offline or (
                        (last_ts > 0 and now - last_ts > STALE_SEC) or
                        (last_ts == 0 and bridge_age > STALE_SEC + 30)
                    )

                    # aggiorna flag reale solo qui
                    self._device_force_offline = bool(debounced_offline)
                    if is_stale:
                        if self.local:
                            self.local.publish(AVAIL_TOPIC, AVAIL_PAYLOAD_OFF, qos=0, retain=True)
                    else:
                        if self.local:
                            self.local.publish(AVAIL_TOPIC, AVAIL_PAYLOAD_ON, qos=0, retain=True)
            except Exception:
                pass

            time.sleep(5)
    Bridge._watchdog_loop = _watchdog_loop

    def run_forever(self):
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log.debug("Shutting down bridge...")
            self.stop.set()
            # Pubblica offline esplicitamente prima di chiudere
            if self.local:
                self.local.publish(AVAIL_TOPIC, AVAIL_PAYLOAD_OFF, qos=0, retain=True)
                time.sleep(0.5)
                self.local.disconnect()
            log.debug("Bridge stopped")

    Bridge.run_forever = run_forever
    return Bridge
