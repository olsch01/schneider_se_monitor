# SE-Monitor cloud API (reverse-engineered)

Captured from SE-Monitor iOS 3.0.6 (app_name `SE_HOME`), 2026-11-24. The app is a
Capacitor (web) app; the backend is **Clipsal Cortex** (Schneider's Clipsal Solar platform),
which in turn proxies the legacy Sense realtime feed for green Wiser/Schneider monitors.

## Hosts

| Purpose | Host |
|---|---|
| REST API | `https://app-api-us.clipsalcortex.com/v1/` |
| Realtime websocket | `wss://app-ws-us.clipsalcortex.com/?token=<id_token>` |
| Cognito hosted UI | `https://clippy-prod.auth.us-east-1.amazoncognito.com` |
| Cognito IdP API | `https://cognito-idp.us-east-1.amazonaws.com/` |
| Upstream identity (Auth0, "SE ID") | `https://ecostruxure.se.app/api/identity/*` → `https://idp.se.com` |

## Auth

1. Cognito hosted UI `oauth2/authorize` with PKCE, `client_id=3o2v8tokrthvv2uk0mj3u0sgva`
   (public client, no secret), `identity_provider=EIM`, `redirect_uri=schneiderhome://`,
   scope `openid profile email aws.cognito.signin.user.admin`.
2. Cognito federates to `app-api-us.clipsalcortex.com/v1/eim_idp_login_redirect`
   → `ecostruxure.se.app/api/identity/authorize` (connection `IDPNEXT-MFA-OAUTH2`,
   audience `https://etpx.restapi`) → `idp.se.com/authorize` (Auth0, client `qhhudHnlWfAFZlZG0lthaJFGnmLCQFAz`).
   **The username/password form step is not yet captured** (SSO cookie skipped it).
3. Back at Cognito: `POST oauth2/token` `grant_type=authorization_code` + `code_verifier`
   → `access_token` (1h), `id_token`, `refresh_token`.
4. Refresh: `POST cognito-idp` with `X-Amz-Target: AWSCognitoIdentityProviderService.InitiateAuth`,
   body `{"ClientId": ..., "AuthFlow": "REFRESH_TOKEN_AUTH", "AuthParameters": {"REFRESH_TOKEN": ...}}`.
5. The app also calls `POST /v1/eim_refresh_token {"refresh_token": <auth0 refresh>}` and stores the
   upstream Auth0 tokens in Cognito custom user attributes (`custom:id_token`, `custom:refresh_token`, ...).
   Whether the REST API requires this is TBD.

REST requests: `Authorization: Bearer <cognito id_token>`. Some calls also send a static
`x-api-key: PudhZveaHq9gTjxNU0XjJ7Tiy520eqQI20bKi683` (embedded in the app).

## REST endpoints (all GET unless noted)

| Endpoint | Notes |
|---|---|
| `POST version_check` | `{"platform":"IOS","version":"3.0.6","app_name":"SE_HOME"}` |
| `user` | user_id, tenant_id, email, role |
| `sites?limit=&offset=` | `{item_count, data:[{site_id, site_name, timezone, has_solar, ...}]}` |
| `sites/{site_id}` | adds `devices[]` (serial, model "Schneider Energy Monitor", operating_state), battery flags, monitoring_start |
| `sites/{id}/appliances` | detected devices: `appliance_id`, `assignment` (e.g. `load_refrigerator__1`), `display_name`, `circuits`. `appliance_id 0` = "Rest of Home" |
| `sites/{id}/power_usage?from_datetime=&to_datetime=` | ISO UTC; 15-min rows: `reading_datetime, grid, consumed` (kW) + `appliances[{id, power}]` |
| `sites/{id}/energy_usage?start_date=&end_date=&groupby=day` | also `from_date/to_date`, `groupby=day|week|month`; `grid, consumed, imported, exported` (kWh) + `appliances[{id, energy}]` |
| `sites/{id}/costs?start_date=&end_date=&groupby=day` | components import/supply_charge/export, per-assignment costs, total_cost |
| `sites/{id}/savings`, `weather`, `energy_mode`, `consent` | misc |
| `sense/sites/{id}/switches` | smart plugs (empty here) |
| `notifications`, `PUT notifications/token` | push |

## Realtime websocket

Connect `wss://app-ws-us.clipsalcortex.com/?token=<id_token>`, send
`{"action": "subscribeSiteLiveData", "clipsal_solar_id": <site_id>}`.

Server messages (`type`): `site_device_status`, `info`, `open` ("Connection to Sense opened"),
then `realtime_update` roughly every 2 s:

```json
{"type": "realtime_update", "payload": {
  "clipsal_solar_id": 12345, "epoch": 1795563421,
  "consumption": 693.88, "ac_load_net": 693.88, "solar": 0.0, "hybrid_inverter": null,
  "switches": [],
  "devices": [{"appliance_id": 467912, "assignment": "load_other__4", "circuit_id": 483219,
               "oem_circuit_id": "99e3f7b7", "w": 270.34, "name": "Water Heater", "attr": null}]}}
```

`consumption` is watts; `devices[].w` is null when the device is off.
