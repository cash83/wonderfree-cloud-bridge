# 🔋 Wonderfree Cloud Bridge

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-add--on-blue?logo=home-assistant)](https://www.home-assistant.io/)
[![Platform](https://img.shields.io/badge/platform-aarch64%20%7C%20amd64%20%7C%20armv7-lightgrey)]()
[![License](https://img.shields.io/badge/license-MIT-green)]()
[![Unofficial](https://img.shields.io/badge/status-unofficial%20%2F%20community-orange)]()

> ⚠️ **PROGETTO NON UFFICIALE — DISCLAIMER**
>
> Questo add-on è un progetto open-source della community e **non è affiliato, approvato o sponsorizzato** da Wonderfree, Landbook, Landecia o Acceleronix.
> I marchi Wonderfree®, Landbook® e Landecia® appartengono ai rispettivi proprietari.
> L'uso di questo software avviene sotto la propria responsabilità.
> Per il supporto ufficiale dei dispositivi fare riferimento alle app e ai canali ufficiali dei produttori.

---

**Add-on per Home Assistant** che integra le power station compatibili Acceleronix (Wonderfree, Landbook, Landecia…) in Home Assistant tramite MQTT, pubblicando automaticamente sensori, switch e controlli.

> **Dalla versione 1.6.0** non è più necessario inserire parametri tecnici: basta email, password e nome app. Il bridge trova tutto il resto da solo.

---

## 📱 Prerequisiti — Account ufficiale

Per usare questo bridge è necessario avere **già un account attivo** sull'app ufficiale del tuo dispositivo e aver completato la registrazione del dispositivo tramite l'app:

| Dispositivo | App ufficiale | Store |
|---|---|---|
| **Wonderfree** | App Wonderfree | [App Store](https://apps.apple.com) / [Google Play](https://play.google.com) |
| **Landbook** | App Landbook | [App Store](https://apps.apple.com) / [Google Play](https://play.google.com) |
| **Landecia** | App Landecia | [App Store](https://apps.apple.com) / [Google Play](https://play.google.com) |

> ✅ **Il bridge usa le stesse credenziali dell'app ufficiale** (email e password). Non crea account separati e non aggira alcuna protezione — si autentica esattamente come fa l'app.
>
> ❌ **Non è possibile usare questo bridge senza un account ufficiale valido.**

---

## ✨ Funzionalità

- 🔌 **Zero configurazione tecnica** — solo email, password e piattaforma
- 📡 **Auto-discovery** — il bridge trova `device_key` e `product_key` automaticamente
- ⚡ **Aggiornamenti ogni ~15 secondi** tramite MQTT cloud + polling HTTP
- 🏠 **MQTT Discovery** — sensori e controlli appaiono automaticamente in HA
- 🔄 **Token refresh automatico** — nessun logout inaspettato
- 🌍 **Multi-piattaforma** — Wonderfree, Landbook, Landecia, Nord America, Europa, Cina

---

## 🚀 Installazione

1. In Home Assistant vai su **Impostazioni → Add-on → Store add-on**
2. Clicca sui tre punti in alto a destra → **Repository**
3. Aggiungi l'URL di questo repository
4. Installa **Wonderfree Cloud Bridge**
5. Vai su **Configurazione** e inserisci i tuoi dati
6. Avvia l'add-on

---

## ⚙️ Configurazione

### Campi obbligatori

| Campo | Descrizione | Esempio |
|---|---|---|
| `wf_email` | Email dell'account app | `utente@email.com` |
| `wf_password` | Password dell'account app | `MiaPassword123` |
| `app` | Nome app con cui hai registrato il dispositivo | `wonderfree` |

### Valori disponibili per `app`

| `app` | Nome app | Backend |
|---|---|---|
| `wonderfree` | **Wonderfree** | acceleronix.io |
| `landbook` | **Landbook** | netprisma.us |
| `landecia` | **Landecia** | landecia.com |
| `europe` | Acceleronix Europa | acceleronix.io |
| `northamerica` | Netprisma Nord America | netprisma.us |
| `china` | Quectel Cina | quectelcn.com |

> 💡 **Non sai quale scegliere?** Guarda con quale app hai registrato il dispositivo:
> - App Wonderfree → `wonderfree`
> - App Landbook → `landbook`

### MQTT

Il bridge si connette al broker MQTT locale di Home Assistant. Con **Mosquitto** (add-on ufficiale) lascia i valori di default:

| Campo | Default | Descrizione |
|---|---|---|
| `mqtt_host` | `core-mosquitto` | Host broker MQTT |
| `mqtt_port` | `1883` | Porta broker MQTT |
| `mqtt_user` | *(vuoto)* | Utente MQTT |
| `mqtt_pass` | *(vuoto)* | Password MQTT |

---

## 🏗️ Architettura

```
┌─────────────────────────────────────────────┐
│              Home Assistant                  │
│                                             │
│  ┌──────────────┐    MQTT Discovery         │
│  │   Entities   │◄─────────────────────┐   │
│  │  (sensori,   │                      │   │
│  │   switch…)   │                      │   │
│  └──────┬───────┘              ┌────────────┤
│         │ comandi              │   Bridge   │
│         └──────────────────►  │  (add-on)  │
│                               └─────┬──────┘
└─────────────────────────────────────┼───────┘
                                      │ WebSocket TLS
                          ┌───────────▼──────────┐
                          │    Cloud Acceleronix  │
                          │  (MQTT + REST API)    │
                          └───────────────────────┘
                                      │
                          ┌───────────▼──────────┐
                          │   Power Station       │
                          │  (Wonderfree, ecc.)   │
                          └──────────────────────┘
```

---

## 🔧 Come funziona l'auto-discovery

All'avvio il bridge esegue questi passi automaticamente:

1. **Legge** email, password e nome app dal config
2. **Carica** tutti i parametri tecnici per l'app scelta (URL API, WebSocket, dominio, chiave firma)
3. **Login** con le credenziali → ottiene il token JWT
4. **Scopre il dispositivo** tramite le API cloud → ricava `device_key` e `product_key`
5. **Salva** i valori in `/data/discovered.json` (cache 30 giorni): i riavvii successivi sono istantanei
6. **Avvia il bridge** con tutti i parametri pronti

---

## 📊 Aggiornamento dati

Il bridge combina tre fonti per mantenere i sensori aggiornati:

| Fonte | Frequenza | Descrizione |
|---|---|---|
| **MQTT cloud** | Continuo | Push in tempo reale dal cloud via WebSocket TLS |
| **Refresh BUS (0x009A)** | ~150-200s | Il bridge richiede nuovi dati al device |
| **Heartbeat autonomo** | ~30s | Il device invia aggiornamenti da solo |

La frequenza effettiva di aggiornamento in Home Assistant è circa **ogni 15 secondi**.

> ℹ️ Se un sensore mostra "X minuti fa" in HA, è perché HA visualizza l'ora dell'ultimo **cambio di valore**, non dell'ultimo aggiornamento. Se il valore è stabile è normale.

---

## ⚙️ Parametri avanzati (opzionali)

| Campo | Default | Descrizione |
|---|---|---|
| `poll_min` / `poll_max` | `30` / `60` | Intervallo polling HTTP (secondi) |
| `refresh_min` / `refresh_max` | `150` / `200` | Intervallo refresh BUS (secondi) |
| `stale_sec` | `250` | Secondi prima di considerare i dati obsoleti |
| `publish_only_changed` | `true` | Pubblica MQTT solo se il valore cambia |
| `dedup_ms` | `200` | Deduplicazione eventi ravvicinati (ms) |
| `send_strategy` | `auto` | Routing comandi: `auto`, `cloud`, `local`, `both` |
| `observer_only` | `false` | Solo lettura: nessun comando inviato |
| `clear_retained` | `false` | Pulisce i topic retained all'avvio |
| `log_level` | `INFO` | Livello log: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `output_power_min/max/step` | `100`/`800`/`10` | Range slider potenza uscita (W) |

---

## 🐛 Debug

Nei log dell'add-on cerca questi messaggi:

| Messaggio | Significato |
|---|---|
| `[AUTODISCOVERY] Login OK` | ✅ Credenziali corrette |
| `[AUTODISCOVERY] device_key=...` | ✅ Dispositivo trovato |
| `Login OK; token acquired` | ✅ Token operativo |
| `[REMOTE] connected` | ✅ Connesso al cloud MQTT |
| `[TX] BUS refresh sent` | ✅ Refresh dati inviato |
| `[REALTIME] ... age=...s` | ✅ Dati ricevuti dal cloud |

---

## 🧩 Struttura moduli

| File | Ruolo |
|---|---|
| `bridge.py` | Entry point |
| `bridge_core.py` | Loop principale, polling HTTP, discovery HA |
| `wf_autodiscovery.py` | Auto-discovery credenziali e dispositivo |
| `wf_config.py` | Configurazione da variabili d'ambiente / options.json |
| `wf_mqtt.py` | Connessione MQTT cloud (WebSocket TLS) e locale |
| `wf_sensors.py` | Normalizzazione sensori, publish HA |
| `wf_switches.py` | Gestione comandi switch/select da HA → cloud |
| `wf_token.py` | Login e refresh token JWT |
| `wf_crypto.py` | Cifratura AES-CBC per autenticazione |

---

## 🔒 Privacy

Quando condividi log o screenshot per supporto:
- Oscura email e password
- Oscura il token Bearer
- Oscura `device_key` se vuoi mantenerlo privato

---

## 📄 Licenza

MIT — vedi [LICENSE](LICENSE)
