# Auto-split from original bridge.py
# Module: wf_mqtt
from __future__ import annotations

import json
import time
import threading
import uuid

import paho.mqtt.client as mqtt

from wf_config import (
    log,
    # local mqtt
    LOCAL_HOST, LOCAL_PORT, LOCAL_USER, LOCAL_PASS,
    LOCAL_OUT_PREFIX,
    AVAIL_TOPIC, AVAIL_PAYLOAD_OFF,
    # cloud
    CLOUD_JWT_MODE, ACCEL_CLIENT,
    PRODUCT_KEY, DEVICE_KEY,
)
from wf_crypto import normalize_bearer


def attach(Bridge):
    def _forward_local_to_cloud(self, msg):
        if not self.remote:
            return
        rtopic = msg.topic[len(LOCAL_OUT_PREFIX):]
        self.remote.publish(rtopic, payload=msg.payload, qos=0, retain=False)
    Bridge._forward_local_to_cloud = _forward_local_to_cloud

    def _on_remote_connect(self, client, userdata, flags, rc, properties=None):
        mode = "JWT as PASSWORD" if CLOUD_JWT_MODE != "username" else "JWT as USERNAME"
        if rc == 0:
            log.info(f"REMOTE connected {self.rhost}:{self.rport}{self.rpath} ({mode})")

            try:
                # Topic bases corretti
                base_topic_slash   = f"q/2/d/qd/{PRODUCT_KEY}/{DEVICE_KEY}"
                base_topic_compact = f"q/2/d/qd{PRODUCT_KEY}{DEVICE_KEY}"

                suffixes = ["/bus", "/ack_", "/onl_", "/loc_", "/ota_", "/bid_"]

                topics_to_sub = []

                # 1) Subscribe suffix specifici (entrambi i formati)
                for base in (base_topic_slash, base_topic_compact):
                    for s in suffixes:
                        topics_to_sub.append((f"{base}{s}", 0))

                # 2) Wildcard fallback (cattura eventuali varianti)
                topics_to_sub.append((f"{base_topic_slash}/#", 0))
                topics_to_sub.append((f"{base_topic_compact}/#", 0))

                # 3) Subscribe ramo user/session (q/1/u/<client>/#)
                try:
                    cid = client._client_id
                    if isinstance(cid, (bytes, bytearray)):
                        cid = cid.decode("utf-8", errors="replace")
                    cid = str(cid).strip()
                    if cid:
                        topics_to_sub.append((f"q/1/u/{cid}/#", 0))
                except Exception:
                    pass

                # Deduplica mantenendo ordine
                seen = set()
                deduped = []
                for t, qos in topics_to_sub:
                    if t not in seen:
                        seen.add(t)
                        deduped.append((t, qos))

                log.info(f"[REMOTE] Subscribing to {len(deduped)} topics (suffixes + wildcards)...")
                client.subscribe(deduped)

            except Exception as e:
                log.warning(f"[REMOTE] subscribe failed: {e}")

        elif rc in (2, 5):
            log.warning(f"[REMOTE] rc={rc} ({mqtt.error_string(rc)}) → router reboot/IP change detected")
            for attempt in range(1, 11):
                log.info(f"[AUTH] retry login after rc={rc} (attempt {attempt})... waiting 30s")
                time.sleep(30)
                try:
                    self.token_mgr.login()
                    log.info("[REMOTE] new token OK, reconnecting...")
                    self._connect_remote()
                    return
                except Exception as e:
                    log.warning(f"[REMOTE] relogin attempt {attempt} failed: {e}")
            log.error("[REMOTE] too many relogin attempts, stopping retries")

        else:
            log.warning(f"REMOTE connect failed rc={rc} ({mqtt.error_string(rc)}) – retry in 30s")
            threading.Timer(30.0, self._connect_remote).start()

    Bridge._on_remote_connect = _on_remote_connect

    def _on_remote_disconnect(self, client, userdata, rc, properties=None, reason=None):
        reason_str = mqtt.error_string(rc) if rc is not None else "unknown"
        if reason:
            reason_str += f" | reason={reason}"
        log.warning(f"[REMOTE] Disconnected from cloud (rc={rc}: {reason_str}) – waiting for network...")
    Bridge._on_remote_disconnect = _on_remote_disconnect

    def _on_remote_message(self, client, userdata, msg):
        """
        Gestisce i messaggi in arrivo dal cloud via WebSocket.

        NOVITA': parsare i topic /ack_ che contengono JSON già decodificato
        con tutti i KV del dispositivo (battery_data, grid_data, pv_data ecc.).
        Questi arrivano automaticamente ogni ~30s senza bisogno di poll HTTP.

        Struttura messaggio /ack_:
          {"data": {"kv": {...}}}
        oppure:
          {"kv": {...}}
        """
        try:
            topic = msg.topic

            if not hasattr(self, "_rt_count"):
                self._rt_count = 0
                self._rt_last_log = 0.0
                self._burst_until = 0.0
            if not hasattr(self, "_ack_kv_buffer"):
                self._ack_kv_buffer = {}
                self._ack_burst_ts = 0.0

            self._rt_count += 1
            now = time.time()

            if now - self._rt_last_log >= 10:
                log.debug(f"[REMOTE] RX realtime: {self._rt_count} msg/10s (last topic: {topic})")
                self._rt_count = 0
                self._rt_last_log = now

            payload = msg.payload or b""
            hx = payload.hex()

            # BUS frame 13 byte → trigger BURST log
            if topic.endswith("/bus") and len(payload) == 13 and hx.startswith("aaaa0009"):
                self._burst_until = now + 5.0

            if now < self._burst_until and "/sys_" not in topic:
                log.debug(f"[REMOTE] BURST topic={topic} len={len(payload)} hex={payload[:64].hex()}")

            if topic.endswith("/bus"):
                log.debug(f"[REMOTE] BUS payload len={len(payload)} hex={payload[:32].hex()}")

            # --- PARSING /onl_: notifica online/offline immediata dal cloud ---
            if topic.endswith("/onl_"):
                try:
                    j = json.loads(payload.decode("utf-8", errors="ignore"))
                    d = j.get("data") or {}
                    val = d.get("value") if isinstance(d, dict) else None
                    if val is not None:
                        if int(val) == 0:
                            # Ignora value=0 nei primi 15s: sono retained da sessioni precedenti
                            startup_age = now - getattr(self, "start_t", now)
                            if startup_age < 15:
                                log.debug(f"[ONL] Ignoring offline (value=0) during startup grace ({startup_age:.1f}s)")
                            else:
                                # Device spento: offline immediato e blocca HTTP dall'overridare
                                log.info("[ONL] Device offline (value=0) -> AVAIL OFF immediato")
                                self._device_force_offline = True
                                self.last_publish_ts = 0
                                self._last_ack_ts = 0
                                if self.local:
                                    from wf_config import AVAIL_TOPIC, AVAIL_PAYLOAD_OFF
                                    self.local.publish(AVAIL_TOPIC, AVAIL_PAYLOAD_OFF, qos=0, retain=True)
                        else:
                            # Device acceso: rimuovi blocco offline, aggiorna _last_ack_ts
                            log.info(f"[ONL] Device online (value={val})")
                            self._device_force_offline = False
                            self._last_ack_ts = now
                except Exception as e:
                    log.debug(f"[ONL] parse error: {e}")

            # --- PARSING /ack_: JSON con KV sensori ---
            if topic.endswith("/ack_"):
                try:
                    j = json.loads(payload.decode("utf-8", errors="ignore"))

                    # Estrai kv dal JSON (varie strutture possibili)
                    kv = None
                    d = j.get("data") or {}
                    if isinstance(d, dict):
                        kv = d.get("kv")
                    if not kv:
                        kv = j.get("kv")
                    if not kv:
                        inner = d.get("data") or {}
                        if isinstance(inner, dict):
                            kv = inner.get("kv")

                    if kv and isinstance(kv, dict):
                        # Accumula KV nel buffer (più messaggi per burst)
                        if now - self._ack_burst_ts > 3.0:
                            self._ack_kv_buffer = {}
                        self._ack_burst_ts = now
                        self._ack_kv_buffer.update(kv)

                        # Pubblica immediatamente con i dati accumulati
                        self._publish_from_ack_kv(self._ack_kv_buffer, now)

                except Exception as e:
                    log.debug(f"[ACK] parse error on {topic}: {e}")

        except Exception as e:
            log.warning(f"[REALTIME] Error processing message: {e}")
    Bridge._on_remote_message = _on_remote_message

    def _publish_from_ack_kv(self, kv: dict, ts: float):
        """
        Normalizza e pubblica su MQTT locale i KV arrivati via /ack_ WebSocket.
        Stesso pipeline del poll HTTP: _normalize_state → _publish_state → _reconcile_switch_states.
        """
        try:
            if not self.local:
                return

            # NON passare ts (now) come dev_update: causerebbe last_publish_ts = now
            # anche quando il device è spento e il cloud invia dati cached.
            # updated_at = 0 → last_publish_ts aggiornato solo dalla via HTTP (timestamp reale).
            st = self._normalize_state(kv, None)
            st["_ts"] = int(ts)  # forza HA ad aggiornare il timestamp "X secondi fa"

            self._publish_state(st, kv, debug=False)
            self._reconcile_switch_states(st)
            self._last_ack_ts = ts  # usato dal poll loop per skip HTTP quando ACK è fresco

            log.debug(
                f"[ACK→HA] battery={st.get('battery_percentage')}% "
                f"in={st.get('total_input_power')}W "
                f"out={st.get('total_output_power')}W "
                f"grid_pwr={st.get('ac_input_power')}W"
            )

        except Exception as e:
            log.debug(f"[ACK→HA] publish error: {e}")
    Bridge._publish_from_ack_kv = _publish_from_ack_kv

    def _connect_remote(self):
        try:
            if getattr(self, "remote", None) is not None:
                self.remote.loop_stop()
                self.remote.disconnect()
                self.remote = None
                log.debug("[REMOTE] previous client closed.")
        except Exception as e:
            log.warning(f"[REMOTE] error closing previous client: {e}")

        tok = self.token_mgr.ensure()
        jwt = normalize_bearer(tok)

        client_id = ACCEL_CLIENT or f"qu_{uuid.uuid4().hex[:6].upper()}_{int(time.time()*1000)}"

        cli = mqtt.Client(
            client_id=client_id,
            transport="websockets",
            protocol=mqtt.MQTTv311,
        )

        if CLOUD_JWT_MODE == "username":
            cli.username_pw_set(username=jwt, password="")
        else:
            cli.username_pw_set(username="", password=jwt)

        cli.ws_set_options(path=self.rpath)
        cli.tls_set()

        cli.on_connect = self._on_remote_connect
        cli.on_disconnect = self._on_remote_disconnect
        cli.on_message = self._on_remote_message

        try:
            cli.connect(self.rhost, self.rport, keepalive=25)
            cli.loop_start()
            self.remote = cli
            log.info(f"[REMOTE] Connected to {self.rhost}:{self.rport}{self.rpath} as {client_id} (mode={CLOUD_JWT_MODE})")
        except Exception as e:
            log.error(f"[REMOTE] connect failed: {e}")
            threading.Timer(10.0, self._connect_remote).start()

    Bridge._connect_remote = _connect_remote

    def _connect_local(self):
        cli = mqtt.Client(protocol=mqtt.MQTTv311)

        if LOCAL_USER:
            cli.username_pw_set(LOCAL_USER, LOCAL_PASS or "")

        cli.will_set(AVAIL_TOPIC, payload=AVAIL_PAYLOAD_OFF, qos=0, retain=True)
        cli.on_connect = self._on_local_connect
        cli.on_message = self._on_local_message
        cli.connect(LOCAL_HOST, LOCAL_PORT, keepalive=60)
        cli.loop_start()
        self.local = cli

    Bridge._connect_local = _connect_local

    return Bridge
