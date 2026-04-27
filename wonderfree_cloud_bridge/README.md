# Wonderfree Cloud Bridge

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-add--on-blue?logo=home-assistant)](https://www.home-assistant.io/)
[![Platform](https://img.shields.io/badge/platform-aarch64%20%7C%20amd64%20%7C%20armv7%20%7C%20armhf%20%7C%20i386-lightgrey)]()
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Unofficial](https://img.shields.io/badge/status-unofficial%20%2F%20community-orange)]()

> **PROGETTO NON UFFICIALE - DISCLAIMER**
>
> Questo add-on e un progetto open-source della community e **non e affiliato, approvato o sponsorizzato** da Wonderfree, Landbook, Landecia o Acceleronix.
> I marchi Wonderfree, Landbook e Landecia appartengono ai rispettivi proprietari.
> L'uso di questo software avviene sotto la propria responsabilita.
> Per il supporto ufficiale dei dispositivi, fai riferimento alle app e ai canali ufficiali dei produttori.

---

**Add-on per Home Assistant** che integra le power station compatibili Acceleronix, incluse Wonderfree, Landbook e Landecia, tramite MQTT. Il bridge pubblica automaticamente sensori, switch e controlli in Home Assistant.

> **Dalla versione 1.6.0** non e piu necessario inserire parametri tecnici: bastano email, password e app utilizzata. Il bridge trova automaticamente il resto.

---

## Prerequisiti - Account ufficiale

Per usare questo bridge devi avere **un account attivo** nell'app ufficiale del dispositivo e aver gia completato la registrazione della power station tramite l'app.

| Dispositivo | App ufficiale | Store |
|---|---|---|
| **Wonderfree** | Wonderfree | [App Store](https://apps.apple.com) / [Google Play](https://play.google.com) |
| **Landbook** | Landbook | [App Store](https://apps.apple.com) / [Google Play](https://play.google.com) |
| **Landecia** | Landecia | [App Store](https://apps.apple.com) / [Google Play](https://play.google.com) |

> **Il bridge usa le stesse credenziali dell'app ufficiale**: email e password. Non crea account separati e non aggira alcuna protezione.
>
> **Non e possibile usare questo bridge senza un account ufficiale valido.**

---

## Funzionalita

- **Zero configurazione tecnica**: solo email, password e app
- **Auto-discovery**: rileva automaticamente `device_key` e `product_key`
- **Aggiornamenti frequenti** tramite MQTT cloud e polling HTTP
- **MQTT Discovery**: sensori e controlli appaiono automaticamente in Home Assistant
- **Token refresh automatico**: rinnovo del token senza interventi manuali
- **Supporto multi-piattaforma**: Wonderfree, Landbook, Landecia, Europa, Nord America e Cina

---

## Installazione

1. In Home Assistant vai su **Impostazioni -> Add-on -> Store add-on**.
2. Apri il menu con i tre punti in alto a destra e seleziona **Repository**.
3. Aggiungi questo URL:

   ```text
   https://github.com/cash83/wonderfree-cloud-bridge
   ```

4. Installa **Wonderfree Cloud Bridge**.
5. Vai nella scheda **Configurazione** e inserisci i tuoi dati.
6. Avvia l'add-on.

---

## Configurazione

### Campi obbligatori

| Campo | Descrizione | Esempio |
|---|---|---|
| `wf_email` | Email dell'account app | `utente@email.com` |
| `wf_password` | Password dell'account app | `MiaPassword123` |
| `app` | App con cui hai registrato il dispositivo | `wonderfree` |

### Valori disponibili per `app`

| `app` | Nome app / area | Backend |
|---|---|---|
| `wonderfree` | Wonderfree | acceleronix.io |
| `landbook` | Landbook | netprisma.us |
| `landecia` | Landecia | landecia.com |
| `europe` | Acceleronix Europa | acceleronix.io |
| `northamerica` | Netprisma Nord America | netprisma.us |
| `china` | Quectel Cina | quectelcn.com |

Se non sai quale valore scegliere, usa l'app con cui hai registrato il dispositivo:

- App Wonderfree -> `wonderfree`
- App Landbook -> `landbook`
- App Landecia -> `landecia`

### MQTT

Il bridge si connette al broker MQTT locale di Home Assistant. Con l'add-on ufficiale **Mosquitto broker**, puoi lasciare i valori predefiniti.

| Campo | Default | Descrizione |
|---|---|---|
| `mqtt_host` | `core-mosquitto` | Host del broker MQTT |
| `mqtt_port` | `1883` | Porta del broker MQTT |
| `mqtt_user` | vuoto | Utente MQTT |
| `mqtt_pass` | vuoto | Password MQTT |

---

## Architettura

```text
Home Assistant
  |
  | MQTT Discovery / comandi
  v
Wonderfree Cloud Bridge add-on
  |
  | WebSocket TLS + REST API
  v
Cloud Acceleronix / Netprisma / Quectel
  |
  v
Power Station
```

---

## Come funziona l'auto-discovery

All'avvio il bridge esegue questi passaggi:

1. Legge email, password e app dalla configurazione dell'add-on.
2. Carica i parametri tecnici della piattaforma scelta.
3. Esegue il login e ottiene il token JWT.
4. Cerca il dispositivo associato all'account.
5. Ricava `device_key` e `product_key`.
6. Salva i valori in `/data/discovered.json` con cache di 30 giorni.
7. Avvia il bridge con tutti i parametri necessari.

---

## Aggiornamento dati

Il bridge combina piu fonti per mantenere i sensori aggiornati.

| Fonte | Frequenza | Descrizione |
|---|---|---|
| MQTT cloud | Continuo | Push in tempo reale via WebSocket TLS |
| Refresh BUS `0x009A` | ~150-200 s | Richiede nuovi dati al dispositivo |
| Heartbeat autonomo | ~30 s | Il dispositivo invia aggiornamenti spontanei |

La frequenza effettiva di aggiornamento in Home Assistant e di circa 15 secondi, a seconda dello stato del dispositivo e del cloud.

> Se un sensore mostra "X minuti fa" in Home Assistant, significa che Home Assistant sta mostrando l'ora dell'ultimo cambio di valore, non necessariamente l'ora dell'ultimo aggiornamento ricevuto.

---

## Parametri avanzati

| Campo | Default | Descrizione |
|---|---|---|
| `poll_min` / `poll_max` | `30` / `60` | Intervallo polling HTTP in secondi |
| `refresh_min` / `refresh_max` | `150` / `200` | Intervallo refresh BUS in secondi |
| `stale_sec` | `250` | Secondi prima di considerare i dati obsoleti |
| `publish_only_changed` | `true` | Pubblica solo i valori modificati |
| `dedup_ms` | `200` | Deduplicazione comandi ravvicinati in millisecondi |
| `send_strategy` | `auto` | Routing comandi: `auto`, `cloud`, `local`, `both` |
| `observer_only` | `false` | Modalita sola lettura: nessun comando inviato |
| `clear_retained` | `false` | Pulisce i topic MQTT retained all'avvio |
| `log_level` | `INFO` | Livello log: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `output_power_min/max/step` | `100` / `800` / `10` | Range dello slider potenza uscita in watt |

---

## Debug

Nei log dell'add-on cerca questi messaggi:

| Messaggio | Significato |
|---|---|
| `[AUTODISCOVERY] Login OK` | Credenziali corrette |
| `[AUTODISCOVERY] device_key=...` | Dispositivo trovato |
| `Login OK; token acquired` | Token operativo |
| `[REMOTE] connected` | Connessione al cloud MQTT riuscita |
| `[TX] BUS refresh sent` | Refresh dati inviato |
| `[REALTIME] ... age=...s` | Dati ricevuti dal cloud |

---

## Struttura moduli

| File | Ruolo |
|---|---|
| `bridge.py` | Entry point |
| `bridge_core.py` | Loop principale, polling HTTP e discovery Home Assistant |
| `wf_autodiscovery.py` | Auto-discovery credenziali e dispositivo |
| `wf_config.py` | Configurazione da variabili d'ambiente e `options.json` |
| `wf_mqtt.py` | Connessione MQTT cloud e broker MQTT locale |
| `wf_sensors.py` | Normalizzazione sensori e pubblicazione MQTT |
| `wf_switches.py` | Gestione comandi da Home Assistant verso il cloud |
| `wf_token.py` | Login e rinnovo token JWT |
| `wf_crypto.py` | Cifratura AES-CBC per autenticazione |

---

## Privacy

Quando condividi log o screenshot per supporto:

- oscura email e password
- oscura il token Bearer
- oscura `device_key` se vuoi mantenerlo privato

---

## Licenza

MIT - vedi [LICENSE](LICENSE).
