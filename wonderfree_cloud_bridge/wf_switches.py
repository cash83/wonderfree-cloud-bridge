# Auto-split from original bridge.py
# Module: wf_switches
from __future__ import annotations

import time
import json
import hashlib
from typing import Any

import paho.mqtt.client as mqtt

from wf_config import (
    log,
    DEDUP_MS, SEND_STRATEGY, CMD_GRACE_SECONDS, SELECT_GRACE_SECONDS, OBSERVER_ONLY,
    OUTPUT_MIN, OUTPUT_MAX, OUTPUT_STEP,

    BUS_TOPIC, LOCAL_OUT_PREFIX,

    LED_CMD_TOPIC, LED_STATE_TOPIC,
    AC_CMD_TOPIC, AC_STATE_TOPIC,
    DC_CMD_TOPIC, DC_STATE_TOPIC,
    SCREEN_CMD_TOPIC, SCREEN_STATE_TOPIC,
    GRIDOUT_CMD_TOPIC, GRIDOUT_STATE_TOPIC,
    BEEP_CMD_TOPIC, BEEP_STATE_TOPIC,
    SLOWCHG_CMD_TOPIC, SLOWCHG_STATE_TOPIC,
    MODE_CMD_TOPIC, MODE_STATE_TOPIC,
    OUTPOW_CMD_TOPIC, OUTPOW_STATE_TOPIC,

    MODE_HEX_BY_LABEL,

    LED_ON_HEX, LED_OFF_HEX,
    AC_ON_HEX, AC_OFF_HEX,
    DC_ON_HEX, DC_OFF_HEX,
    SCREEN_ON_HEX, SCREEN_OFF_HEX,
    SCREEN_TIMEOUT_HEX_BY_LABEL,
    GRID_ON_HEX, GRID_OFF_HEX,
    BEEP_ON_HEX, BEEP_OFF_HEX,
    SLOW_ON_HEX, SLOW_OFF_HEX,

    hex_bytes,
)

def attach(Bridge):
    def _on_local_message(self, client, userdata, msg):
        t = msg.topic
        p = (msg.payload or b"").decode(errors="ignore").strip()


        if t == LED_CMD_TOPIC:
            on = p.upper() == "ON"
            self._send_cmd(LED_ON_HEX if on else LED_OFF_HEX, "led_status", LED_STATE_TOPIC, on, CMD_GRACE_SECONDS)
            return
        if t == AC_CMD_TOPIC:
            on = p.upper() == "ON"
            self._send_cmd(AC_ON_HEX if on else AC_OFF_HEX, "ac_switch", AC_STATE_TOPIC, on, CMD_GRACE_SECONDS)
            return
        if t == DC_CMD_TOPIC:
            on = p.upper() == "ON"
            self._send_cmd(DC_ON_HEX if on else DC_OFF_HEX, "dc_switch", DC_STATE_TOPIC, on, CMD_GRACE_SECONDS)
            return
        if t == SCREEN_CMD_TOPIC:
            label = p
            hx = SCREEN_TIMEOUT_HEX_BY_LABEL.get(label)
            if hx:
                self._send_select(hx, "screen_sleeptime_set", SCREEN_STATE_TOPIC, label, SELECT_GRACE_SECONDS)
            else:
                log.warning(f"[CMD] screen_sleeptime_set invalid payload: {p}")
            return
        if t == GRIDOUT_CMD_TOPIC:
            on = p.upper() == "ON"
            self._send_cmd(GRID_ON_HEX if on else GRID_OFF_HEX, "grid_output", GRIDOUT_STATE_TOPIC, on, CMD_GRACE_SECONDS)
            return
        if t == BEEP_CMD_TOPIC:
            on = p.upper() == "ON"
            self._send_cmd(BEEP_ON_HEX if on else BEEP_OFF_HEX, "beep_setting", BEEP_STATE_TOPIC, on, CMD_GRACE_SECONDS)
            return
        if t == SLOWCHG_CMD_TOPIC:
            on = p.upper() == "ON"
            self._send_cmd(SLOW_ON_HEX if on else SLOW_OFF_HEX, "ac_charging_limit", SLOWCHG_STATE_TOPIC, on, CMD_GRACE_SECONDS)
            return

        if t == MODE_CMD_TOPIC:
            label = p
            hx = MODE_HEX_BY_LABEL.get(label)
            if hx:
                self._send_select(hx, "mode_set", MODE_STATE_TOPIC, label, SELECT_GRACE_SECONDS)
            return

        if t == OUTPOW_CMD_TOPIC:
            try:
                val = int(float(p))
            except Exception:
                log.warning(f"[CMD] output_power_set invalid payload: {p}")
                return

            val = max(OUTPUT_MIN, min(OUTPUT_MAX, val))
            payload = self._gen_hex_output_power(val)
            self._route_and_publish(payload)
            if self.local:
                self.local.publish(OUTPOW_STATE_TOPIC, str(val).encode(), qos=0, retain=True)
            return

    # ---- Command helpers (NoBeep routing + dedup) ----
    Bridge._on_local_message = _on_local_message

    def _route_and_publish(self, payload: bytes):
        if OBSERVER_ONLY:
            log.debug("[CMD] OBSERVER_ONLY=true, comando ignorato")
            return
        # dedup
        h = hashlib.md5(payload).hexdigest()
        now = time.time()
        if (
            getattr(self, "_last_cmd_hash", None) == h and
            (now - getattr(self, "_last_cmd_time", 0.0)) * 1000 < DEDUP_MS
        ):
            return  # drop duplicate very-close command
        self._last_cmd_hash = h
        self._last_cmd_time = now

        remote_ok = self.remote is not None
        strat = SEND_STRATEGY
        log.info(f"[CMD] route={strat} remote_ok={remote_ok} topic={BUS_TOPIC} hex={payload.hex()}")

        if strat == "cloud":
            if remote_ok:
                self.remote.publish(BUS_TOPIC, payload=payload, qos=0, retain=False)
            return

        if strat == "local":
            if self.local:
                self.local.publish(LOCAL_OUT_PREFIX + BUS_TOPIC, payload, qos=0, retain=False)
            return

        if strat == "both":
            if self.local:
                self.local.publish(LOCAL_OUT_PREFIX + BUS_TOPIC, payload, qos=0, retain=False)
            if remote_ok:
                self.remote.publish(BUS_TOPIC, payload=payload, qos=0, retain=False)
            return

        # auto
        if remote_ok:
            self.remote.publish(BUS_TOPIC, payload=payload, qos=0, retain=False)
        else:
            if self.local:
                self.local.publish(LOCAL_OUT_PREFIX + BUS_TOPIC, payload, qos=0, retain=False)
    Bridge._route_and_publish = _route_and_publish

    def _send_cmd(self, hx: str, key: str, state_topic: str, desired_on: bool, grace: int):
        payload = hex_bytes(hx)
        self._route_and_publish(payload)

        # optimistic state
        self.pending[key] = {"desired": desired_on, "until": time.time() + grace}
        if self.local:
            self.local.publish(state_topic, b"ON" if desired_on else b"OFF", qos=0, retain=True)
        log.debug(f"[CMD] {key} -> {'ON' if desired_on else 'OFF'} (grace {grace}s)")
    Bridge._send_cmd = _send_cmd

    def _send_select(self, hx: str, key: str, state_topic: str, label: str, grace: int):
        payload = hex_bytes(hx)
        self._route_and_publish(payload)
        self.pending[key] = {"desired": label, "until": time.time() + grace}
        if self.local:
            self.local.publish(state_topic, label.encode(), qos=0, retain=True)
        log.debug(f"[CMD] {key} -> {label} (grace {grace}s)")
    Bridge._send_select = _send_select

    def _gen_hex_output_power(self, watts: int) -> bytes:
        watts = max(OUTPUT_MIN, min(OUTPUT_MAX, int(watts)))
        offset = (watts - 100) // 10
        b4  = 0x03 + offset
        b6  = 0xA1 + offset
        b8  = 0x13
        b10 = 0xEA
        ext = 0x01
        low  = watts & 0xFF
        high = (watts >> 8) & 0xFF
        frame = [0xAA,0xAA,0x00,0x0A, b4,0x00, b6,0x00, b8,0x00, b10, ext, high, low]
        log.debug(f"[HEX_GEN] output_power_set {watts}W -> " + " ".join(f"{x:02x}" for x in frame))
        return bytes(frame)

    # ---- Remote MQTT ----
    Bridge._gen_hex_output_power = _gen_hex_output_power

    return Bridge
