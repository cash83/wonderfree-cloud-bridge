# bridge.py - Entry point principale Wonderfree Cloud Bridge
#
# ORDINE IMPORT CRITICO:
#   1. wf_autodiscovery.setup() imposta os.environ con i valori scoperti
#      (wf_domain, device_key, product_key, accel_client) PRIMA che
#      wf_config.py venga importato e legga le variabili d'ambiente.
#   2. Solo dopo si importa wf_config e tutto il resto.

import wf_autodiscovery
wf_autodiscovery.setup()

from wf_config import log
from bridge_core import Bridge

import wf_mqtt
import wf_sensors
import wf_switches

# Attacca le funzioni/metodi al Bridge
wf_mqtt.attach(Bridge)
wf_sensors.attach(Bridge)
wf_switches.attach(Bridge)

if __name__ == "__main__":
    b = Bridge()
    log.info("[INIT] starting...")
    b.start()
    b.run_forever()
