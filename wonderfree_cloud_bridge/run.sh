#!/usr/bin/with-contenv bash
set -e

OPTS="/data/options.json"
ADDON_VERSION="${WF_CLOUD_ADDON_VERSION:-}"
if [ -z "$ADDON_VERSION" ] && [ -f "/app/config.json" ]; then
  ADDON_VERSION="$(jq -r '.version // "unknown"' /app/config.json)"
fi
echo "[INFO] Wonderfree Cloud Bridge starting... (addon v${ADDON_VERSION:-unknown})"
echo "[INFO] Loading options..."

# =============================================================
# CREDENZIALI ACCOUNT (obbligatori)
# =============================================================
WF_EMAIL=$(jq -r '.wf_email // ""' "$OPTS")
WF_PASSWORD=$(jq -r '.wf_password // ""' "$OPTS")
WF_DEVICE_KEY=$(jq -r '.wf_device_key // ""' "$OPTS")

if [ -z "$WF_EMAIL" ];    then echo "[ERRORE] 'wf_email' mancante";    exit 1; fi
if [ -z "$WF_PASSWORD" ]; then echo "[ERRORE] 'wf_password' mancante"; exit 1; fi

# =============================================================
# APP / PIATTAFORMA
# Valori disponibili: wonderfree, landbook, landecia,
#                     europe, northamerica, china,
#                     europe-uat, northamerica-uat, china-uat
# Tutto il resto viene scoperto automaticamente da wf_autodiscovery.py
# =============================================================
PLATFORM=$(jq -r '.app // "wonderfree"' "$OPTS")

# =============================================================
# MQTT
# =============================================================
MQTT_HOST=$(jq -r '.mqtt_host // "core-mosquitto"' "$OPTS")
MQTT_PORT=$(jq -r '.mqtt_port // 1883' "$OPTS")
MQTT_USER=$(jq -r '.mqtt_user // ""' "$OPTS")
MQTT_PASS=$(jq -r '.mqtt_pass // ""' "$OPTS")

HA_BASE=$(jq -r '.ha_base // "wonderfree"' "$OPTS")
DISCOVERY_PREFIX=$(jq -r '.discovery_prefix // "homeassistant"' "$OPTS")
LOG_LEVEL=$(jq -r '.log_level // "INFO"' "$OPTS")

# =============================================================
# PARAMETRI POLLING / TIMING
# =============================================================
POLL_MIN=$(jq -r '.poll_min // 30' "$OPTS")
POLL_MAX=$(jq -r '.poll_max // 60' "$OPTS")
REFRESH_MIN=$(jq -r '.refresh_min // 150' "$OPTS")
REFRESH_MAX=$(jq -r '.refresh_max // 200' "$OPTS")
REFRESH_MIN_GAP=$(jq -r '.refresh_min_gap // 20' "$OPTS")
STARTUP_REFRESH_COUNT=$(jq -r '.startup_refresh_count // 1' "$OPTS")
STARTUP_REFRESH_JITTER_MS=$(jq -r '.startup_refresh_jitter_ms // 1500' "$OPTS")
DISABLE_STARTUP_MASK=$(jq -r '.disable_startup_mask // false' "$OPTS")
MASK_REFRESH_INTERVAL=$(jq -r '.mask_refresh_interval // 600' "$OPTS")
REFRESH_HTTP_DELAY_MS=$(jq -r '.refresh_http_delay_ms // 1800' "$OPTS")
QUICK_REFETCH_AFTER_REFRESH=$(jq -r '.quick_refetch_after_refresh // true' "$OPTS")
SELECT_GRACE_SECONDS=$(jq -r '.select_grace_seconds // 12' "$OPTS")
DEDUP_MS=$(jq -r '.dedup_ms // 200' "$OPTS")
STALE_SEC=$(jq -r '.stale_sec // 250' "$OPTS")
MUTE_POLL=$(jq -r '.mute_poll // false' "$OPTS")
PUBLISH_ONLY_CHANGED=$(jq -r '.publish_only_changed // true' "$OPTS")
PREFER_HTTP_SOC=$(jq -r '.prefer_http_soc // false' "$OPTS")
PREFER_HTTP_TEMP=$(jq -r '.prefer_http_temp // false' "$OPTS")
HTTP_SOC_THRESHOLD=$(jq -r '.http_soc_threshold // 5' "$OPTS")
HTTP_TIMEOUT=$(jq -r '.http_timeout // 6' "$OPTS")
CLEAR_RETAINED=$(jq -r '.clear_retained // false' "$OPTS")
OBSERVER_ONLY=$(jq -r '.observer_only // false' "$OPTS")
STARTUP_BURST_SECONDS=$(jq -r '.startup_burst_seconds // 30' "$OPTS")
STARTUP_BURST_PERIOD=$(jq -r '.startup_burst_period // 3' "$OPTS")
CMD_GRACE_SECONDS=$(jq -r '.cmd_grace_seconds // 8' "$OPTS")
SEND_STRATEGY=$(jq -r '.send_strategy // "auto"' "$OPTS")
OUTPUT_POWER_MIN=$(jq -r '.output_power_min // 100' "$OPTS")
OUTPUT_POWER_MAX=$(jq -r '.output_power_max // 800' "$OPTS")
OUTPUT_POWER_STEP=$(jq -r '.output_power_step // 10' "$OPTS")

# =============================================================
# EXPORT
# =============================================================
export WF_EMAIL WF_PASSWORD WF_DEVICE_KEY PLATFORM
export LOCAL_HOST="$MQTT_HOST" LOCAL_PORT="$MQTT_PORT" LOCAL_USER="$MQTT_USER" LOCAL_PASS="$MQTT_PASS"
export HA_BASE HA_DISCOVERY="$DISCOVERY_PREFIX" LOG_LEVEL
export POLL_MIN POLL_MAX REFRESH_MIN REFRESH_MAX REFRESH_MIN_GAP
export STARTUP_REFRESH_COUNT STARTUP_REFRESH_JITTER_MS DISABLE_STARTUP_MASK
export MASK_REFRESH_INTERVAL REFRESH_HTTP_DELAY_MS QUICK_REFETCH_AFTER_REFRESH
export SELECT_GRACE_SECONDS DEDUP_MS STALE_SEC MUTE_POLL PUBLISH_ONLY_CHANGED
export PREFER_HTTP_SOC PREFER_HTTP_TEMP HTTP_SOC_THRESHOLD HTTP_TIMEOUT
export CLEAR_RETAINED OBSERVER_ONLY
export STARTUP_BURST_SECONDS STARTUP_BURST_PERIOD CMD_GRACE_SECONDS SEND_STRATEGY
export OUTPUT_POWER_MIN OUTPUT_POWER_MAX OUTPUT_POWER_STEP

echo "[INFO] Config loaded successfully"
echo "[INFO] Starting bridge..."

exec python3 /app/bridge.py
