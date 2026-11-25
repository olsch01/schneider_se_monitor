# Schneider SE-Monitor for Home Assistant

A Home Assistant integration for the **Schneider Energy Monitor** (formerly the
**Wiser Energy** monitor, a Schneider-branded Sense monitor).

In 2025 Schneider moved these monitors off the Sense platform and onto its own
**SE-Monitor** app (previously called "Schneider Home"). After the move, the
built-in [Sense integration](https://www.home-assistant.io/integrations/sense/)
can still log in but reports the monitor as offline. This integration connects
to the cloud service the SE-Monitor app uses instead.

> **Unofficial.** This project is not affiliated with or supported by Schneider
> Electric, Clipsal or Sense. It uses an undocumented API that may change
> without notice.

## Features

| Entity | Unit | Updates |
|---|---|---|
| Power | W | Live (websocket), sampled every 10 s |
| Solar power | W | Live (only on sites with solar) |
| Energy today | kWh | Every 5 min |
| Grid import today | kWh | Every 5 min |
| Grid export today | kWh | Every 5 min (only on sites with solar) |
| Energy this month | kWh | Every 5 min |

The energy sensors work with the Home Assistant **Energy dashboard**. Use
*Grid import today* as your grid consumption source.

### Detected devices

Like the Sense integration, each device the monitor has detected (Fridge,
Washer, Well pump, ...) becomes its own Home Assistant device, linked to the
monitor, with:

| Entity | Unit | Notes |
|---|---|---|
| Power | W | Live; 0 when the device is off |
| Energy today | kWh | Every 5 min; usable as an individual device in the Energy dashboard |
| *(on/off)* | binary sensor | On while the device draws power. Handy for "washer finished" automations |

Devices the monitor newly detects (or drops) are picked up within an hour; the
integration reloads itself. Renaming a device in the SE-Monitor app shows up
after the integration is reloaded or Home Assistant restarts.

### When the monitor goes offline

If the monitor loses its internet connection, the live power entities become
**unavailable** after about 3 minutes and Home Assistant logs one warning. The
integration keeps retrying, at most every 2 minutes, and picks back up on its
own when the monitor returns, logging that the feed was restored. Energy
totals keep their last value during the outage and catch up once Schneider's
cloud receives the missed data.

## Requirements

- A Schneider Energy Monitor / Wiser Energy monitor that shows data in the
  **SE-Monitor** app
- Your SE-Monitor account login
- Internet access from Home Assistant (this is a cloud integration)

## Installation

### HACS (recommended)

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/olsch01/schneider_se_monitor` with category **Integration**.
3. Install **Schneider SE-Monitor** and restart Home Assistant.

### Manual

Copy `custom_components/schneider_se_monitor` into your Home Assistant
`config/custom_components/` folder and restart Home Assistant.

## Setup

Schneider's login page uses a captcha, so the integration cannot log in with a
username and password on its own. Instead, you log in once in your browser:

1. Go to **Settings → Devices & Services → Add Integration** and choose
   **Schneider SE-Monitor**.
2. Click the login link in the dialog and sign in with your SE-Monitor account.
3. Your browser is then sent to a `http://localhost/?code=...` page that
   **will not load**. That is expected.
4. Copy the full address from the browser's address bar, paste it into the
   dialog, and submit.

After that, Home Assistant keeps the login fresh by itself. If Schneider ever
expires it, Home Assistant shows a **Re-authenticate** prompt and you repeat
the steps above.

The link in the dialog can be used only once. If a login fails, close the
dialog and start again to get a new link.

## Known limitations

- Energy totals come from 15-minute intervals, so the daily and monthly
  sensors rise in steps.
- Data for the last few minutes before midnight may arrive after midnight and
  be counted only in the monthly total.
- Only the US cloud region (`app-api-us.clipsalcortex.com`) has been tested.
- Per-device energy comes straight from Schneider and is not always
  consistent: a device can report more energy than the whole house. These
  values are passed through unchanged.
- Entity IDs follow the device names, like the Sense integration. If you still
  have Sense entities with the same names, the new ones get a numeric suffix
  (for example `sensor.fridge_power_2`).

## How it works

The SE-Monitor app is built on Schneider's **Clipsal Cortex** platform. Login
goes through AWS Cognito, federated to Schneider's SE ID (Auth0). The REST and
websocket API is documented in [docs/API.md](docs/API.md).

## Development

```bash
uv venv -p 3.14 .venv
uv pip install -p .venv -r requirements_test.txt
.venv/bin/python -m pytest
```

The `tools/` folder holds the scripts used to map the API: a mitmproxy
add-on, a redacting flow exporter and a browser login helper. They read
credentials and tokens only from git-ignored files (`.env`, `.tokens.json`).

## Acknowledgements

Special thanks to the developers of the Home Assistant
[Sense integration](https://www.home-assistant.io/integrations/sense/) and the
[sense_energy](https://github.com/scottbonline/sense) library it is built on,
including [kbickar](https://github.com/kbickar) and
[scottbonline](https://github.com/scottbonline). Their work served as the
model for this integration: its devices, entities and naming follow the Sense
integration so that former Wiser Energy owners get a familiar setup.

## License

MIT
