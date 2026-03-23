#!/usr/bin/with-contenv bashio

export WF_EMAIL=$(bashio::config 'wf_email')
export WF_PASSWORD=$(bashio::config 'wf_password')
export DEVICE_KEY=$(bashio::config 'device_key')
export PRODUCT_KEY=$(bashio::config 'product_key')
export LOCAL_HOST=$(bashio::config 'mqtt_host')
export LOCAL_PORT=$(bashio::config 'mqtt_port')
export LOCAL_USER=$(bashio::config 'mqtt_user')
export LOCAL_PASS=$(bashio::config 'mqtt_pass')
export LOG_LEVEL=$(bashio::config 'log_level')
export POLL_MIN=$(bashio::config 'poll_min')
export POLL_MAX=$(bashio::config 'poll_max')
export STALE_SEC=$(bashio::config 'stale_sec')
export SEND_STRATEGY=$(bashio::config 'send_strategy')

exec python3 /bridge.py
