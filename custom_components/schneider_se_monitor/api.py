"""Async client for the Schneider SE-Monitor cloud (Clipsal Cortex backend).

Self-contained (depends only on aiohttp) so it can be split out into a PyPI
library later. See docs/API.md for the reverse-engineered protocol.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import aiohttp

_LOGGER = logging.getLogger(__name__)

COGNITO_DOMAIN = "https://clippy-prod.auth.us-east-1.amazoncognito.com"
COGNITO_IDP = "https://cognito-idp.us-east-1.amazonaws.com/"
CLIENT_ID = "3o2v8tokrthvv2uk0mj3u0sgva"
IDENTITY_PROVIDER = "EIM"
SCOPE = "openid profile email aws.cognito.signin.user.admin"
REDIRECT_URI = "http://localhost"

API_URL = "https://app-api-us.clipsalcortex.com/v1/"
WS_URL = "wss://app-ws-us.clipsalcortex.com/"

TOKEN_REFRESH_MARGIN = 300  # seconds before expiry to refresh
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


class SEMonitorError(Exception):
    """Base error."""


class SEMonitorAuthError(SEMonitorError):
    """Authentication failed or refresh token is no longer valid."""


class SEMonitorConnectionError(SEMonitorError):
    """Network or server error."""


# --------------------------------------------------------------------------- #
# Login (browser + PKCE)
# --------------------------------------------------------------------------- #


@dataclass
class LoginRequest:
    """A pending browser login. Keep it until the user pastes the redirect URL back."""

    url: str
    state: str
    code_verifier: str
    redirect_uri: str = REDIRECT_URI


def create_login_request(redirect_uri: str = REDIRECT_URI) -> LoginRequest:
    """Build the Cognito authorize URL the user opens in their browser."""
    verifier = secrets.token_urlsafe(96)[:128]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    state = secrets.token_urlsafe(16)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": redirect_uri,
            "identity_provider": IDENTITY_PROVIDER,
            "scope": SCOPE,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return LoginRequest(
        url=f"{COGNITO_DOMAIN}/oauth2/authorize?{query}",
        state=state,
        code_verifier=verifier,
        redirect_uri=redirect_uri,
    )


def parse_redirect_url(redirect_url: str, login: LoginRequest) -> str:
    """Extract the authorization code from the URL the browser landed on."""
    params = dict(parse_qsl(urlsplit(redirect_url.strip()).query))
    if "error" in params:
        raise SEMonitorAuthError(
            f"{params['error']}: {params.get('error_description', '')}".strip(": ")
        )
    if "code" not in params:
        raise SEMonitorAuthError("No authorization code found in URL")
    if params.get("state") != login.state:
        raise SEMonitorAuthError("State mismatch; start the login again")
    return params["code"]


async def exchange_code(
    session: aiohttp.ClientSession, code: str, login: LoginRequest
) -> dict[str, Any]:
    """Exchange the authorization code for Cognito tokens."""
    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "redirect_uri": login.redirect_uri,
        "code_verifier": login.code_verifier,
    }
    try:
        async with session.post(
            f"{COGNITO_DOMAIN}/oauth2/token", data=data, timeout=REQUEST_TIMEOUT
        ) as resp:
            body = await resp.json(content_type=None)
            if resp.status != 200:
                raise SEMonitorAuthError(body.get("error", f"HTTP {resp.status}"))
            return body
    except (aiohttp.ClientError, TimeoutError) as err:
        raise SEMonitorConnectionError(err) from err


# --------------------------------------------------------------------------- #
# Token management
# --------------------------------------------------------------------------- #


def _jwt_exp(token: str) -> float:
    """Return the exp claim of a JWT (unverified; only used for scheduling refresh)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, ValueError):
        return 0.0


class SEMonitorAuth:
    """Holds the Cognito refresh token and hands out fresh id tokens."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        refresh_token: str,
        id_token: str | None = None,
    ) -> None:
        """Initialize."""
        self._session = session
        self.refresh_token = refresh_token
        self._id_token = id_token
        self._expires = _jwt_exp(id_token) if id_token else 0.0
        self._lock = asyncio.Lock()

    async def async_get_id_token(self) -> str:
        """Return a valid id token, refreshing if needed."""
        async with self._lock:
            if self._id_token is None or time.time() > self._expires - TOKEN_REFRESH_MARGIN:
                await self._async_refresh()
            assert self._id_token is not None
            return self._id_token

    def invalidate(self) -> None:
        """Force a refresh on next use (e.g. after a 401)."""
        self._expires = 0.0

    async def _async_refresh(self) -> None:
        payload = {
            "ClientId": CLIENT_ID,
            "AuthFlow": "REFRESH_TOKEN_AUTH",
            "AuthParameters": {"REFRESH_TOKEN": self.refresh_token},
        }
        headers = {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
        }
        try:
            async with self._session.post(
                COGNITO_IDP,
                data=json.dumps(payload),
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                body = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SEMonitorConnectionError(err) from err
        if resp.status != 200:
            err_type = str(body.get("__type", ""))
            if resp.status == 400 and "NotAuthorized" in err_type:
                raise SEMonitorAuthError(body.get("message", err_type))
            raise SEMonitorConnectionError(f"Token refresh failed: HTTP {resp.status} {err_type}")
        result = body["AuthenticationResult"]
        self._id_token = result["IdToken"]
        self._expires = _jwt_exp(self._id_token) or time.time() + result.get("ExpiresIn", 3600)
        if new_refresh := result.get("RefreshToken"):
            self.refresh_token = new_refresh
        _LOGGER.debug("Refreshed SE-Monitor id token")


# --------------------------------------------------------------------------- #
# Data models
# --------------------------------------------------------------------------- #


@dataclass
class Site:
    """A monitored site (home)."""

    site_id: int
    name: str
    timezone: str
    has_solar: bool
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def meter(self) -> dict[str, Any] | None:
        """The first metering device (the energy monitor) if site details were loaded."""
        for dev in self.raw.get("devices", []):
            if dev.get("device_type") == "METER":
                return dev
        return None


@dataclass
class EnergyUsage:
    """Energy totals for one period, in kWh."""

    date: str
    consumed: float | None
    imported: float | None
    exported: float | None
    grid: float | None
    appliances: dict[int, float | None]


@dataclass
class RealtimeData:
    """One realtime_update frame."""

    epoch: int
    consumption: float | None  # W
    solar: float | None  # W
    devices: list[dict[str, Any]]
    received: float = field(default_factory=time.time)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RealtimeData:
        """Parse a websocket payload."""
        return cls(
            epoch=int(payload.get("epoch") or 0),
            consumption=payload.get("consumption"),
            solar=payload.get("solar"),
            devices=payload.get("devices") or [],
        )

    def device_power(self, appliance_id: int) -> float | None:
        """Watts for one appliance: 0 when listed but off, None when not in the frame."""
        for dev in self.devices:
            if dev.get("appliance_id") == appliance_id:
                return float(dev["w"]) if dev.get("w") is not None else 0.0
        return None


# Assignment prefixes that are not real devices.
NON_DEVICE_KINDS = frozenset({"consumption"})  # "mains": duplicates the site total
NAME_OVERRIDES = {"always_on": "Always On"}  # API labels it "Rest of Home" too


@dataclass
class Appliance:
    """A device detected by the monitor."""

    appliance_id: int
    name: str
    assignment: str

    @property
    def kind(self) -> str:
        """Assignment type, e.g. 'load_refrigerator' or 'always_on'."""
        return self.assignment.split("__", 1)[0]

    @property
    def is_device(self) -> bool:
        """False for pseudo entries such as the mains total."""
        return self.kind not in NON_DEVICE_KINDS

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Appliance:
        """Parse an appliances entry."""
        assignment = raw.get("assignment") or ""
        kind = assignment.split("__", 1)[0]
        name = NAME_OVERRIDES.get(kind) or raw.get("display_name") or f"Device {raw['appliance_id']}"
        return cls(appliance_id=int(raw["appliance_id"]), name=name, assignment=assignment)


# --------------------------------------------------------------------------- #
# REST client
# --------------------------------------------------------------------------- #


class SEMonitorClient:
    """REST client."""

    def __init__(self, session: aiohttp.ClientSession, auth: SEMonitorAuth) -> None:
        """Initialize."""
        self._session = session
        self.auth = auth

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in (1, 2):
            token = await self.auth.async_get_id_token()
            try:
                async with self._session.get(
                    API_URL + path,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=REQUEST_TIMEOUT,
                ) as resp:
                    if resp.status in (401, 403):
                        if attempt == 1:
                            self.auth.invalidate()
                            continue
                        raise SEMonitorAuthError(f"HTTP {resp.status} for {path}")
                    if resp.status >= 400:
                        detail = (await resp.text())[:200]
                        raise SEMonitorConnectionError(f"HTTP {resp.status} for {path}: {detail}")
                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError) as err:
                raise SEMonitorConnectionError(err) from err
        raise SEMonitorAuthError("Unauthorized")  # pragma: no cover

    async def async_get_user(self) -> dict[str, Any]:
        """Return the current user."""
        return await self._get("user")

    async def async_get_sites(self) -> list[Site]:
        """Return all sites on the account."""
        sites: list[Site] = []
        offset = 0
        while True:
            body = await self._get("sites", {"limit": 50, "offset": offset})
            data = body.get("data", [])
            sites.extend(_site(s) for s in data)
            offset += len(data)
            if not data or offset >= body.get("item_count", 0):
                return sites

    async def async_get_site(self, site_id: int) -> Site:
        """Return site details, including devices."""
        return _site(await self._get(f"sites/{site_id}"))

    async def async_get_appliances(self, site_id: int) -> list[Appliance]:
        """Return detected appliances (devices), including pseudo entries."""
        return [Appliance.from_api(a) for a in await self._get(f"sites/{site_id}/appliances")]

    async def async_get_energy_usage(
        self, site_id: int, start_date: str, end_date: str, groupby: str = "day"
    ) -> list[EnergyUsage]:
        """Return energy totals per period (inclusive YYYY-MM-DD dates, site-local)."""
        rows = await self._get(
            f"sites/{site_id}/energy_usage",
            {"start_date": start_date, "end_date": end_date, "groupby": groupby},
        )
        return [
            EnergyUsage(
                date=row.get("reading_date") or row.get("reading_datetime", ""),
                consumed=row.get("consumed"),
                imported=row.get("imported"),
                exported=row.get("exported"),
                grid=row.get("grid"),
                appliances={a["id"]: a.get("energy") for a in row.get("appliances", [])},
            )
            for row in rows
        ]

    async def async_get_power_usage(
        self, site_id: int, from_datetime: str, to_datetime: str
    ) -> list[dict[str, Any]]:
        """Return 15-minute average power rows (kW) between two ISO UTC datetimes."""
        return await self._get(
            f"sites/{site_id}/power_usage",
            {"from_datetime": from_datetime, "to_datetime": to_datetime},
        )


def _site(raw: dict[str, Any]) -> Site:
    return Site(
        site_id=int(raw["site_id"]),
        name=raw.get("site_name") or f"Site {raw['site_id']}",
        timezone=raw.get("timezone") or "UTC",
        has_solar=bool(raw.get("has_solar")),
        raw=raw,
    )


# --------------------------------------------------------------------------- #
# Realtime websocket
# --------------------------------------------------------------------------- #


class SEMonitorRealtime:
    """Keeps a websocket open and reports realtime_update frames to a callback."""

    NO_DATA_TIMEOUT = 120.0  # reconnect if subscribed but silent this long (monitor offline)
    BACKOFF_MIN = 5.0
    BACKOFF_MAX = 120.0  # worst-case delay before picking back up after an outage

    def __init__(
        self,
        session: aiohttp.ClientSession,
        auth: SEMonitorAuth,
        site_id: int,
        on_data: Callable[[RealtimeData], None],
        on_auth_error: Callable[[SEMonitorAuthError], None] | None = None,
    ) -> None:
        """Initialize."""
        self._session = session
        self._auth = auth
        self._site_id = site_id
        self._on_data = on_data
        self._on_auth_error = on_auth_error
        self._task: asyncio.Task[None] | None = None
        self.connected = False
        self.last_data: RealtimeData | None = None
        self.first_data = asyncio.Event()
        self.url = WS_URL
        self._session_had_data = False
        self._renewing = False
        self._outage_logged = False
        self._last_error: str | None = None

    def start(self) -> asyncio.Task[None]:
        """Start the background connection loop."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name=f"se_monitor_ws_{self._site_id}")
        return self._task

    async def stop(self) -> None:
        """Stop and close the connection."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.connected = False

    async def run(self) -> None:
        """Connection loop. Runs until cancelled or the login stops working.

        Every disconnect (network loss, server close, monitor offline, token renewal)
        leads to a reconnect. The wait between attempts doubles from BACKOFF_MIN up to
        BACKOFF_MAX and resets as soon as a connection delivers data again.
        """
        backoff = self.BACKOFF_MIN
        while True:
            got_data = False
            try:
                got_data = await self._connect_once()
            except asyncio.CancelledError:
                raise
            except SEMonitorAuthError as err:
                self._set_connected(False, str(err))
                if self._on_auth_error:
                    self._on_auth_error(err)
                return
            except Exception as err:  # noqa: BLE001 - keep the loop alive whatever happens
                got_data = got_data or self._session_had_data
                _LOGGER.debug("SE-Monitor realtime connection ended: %r", err)
                self._last_error = str(err) or type(err).__name__
            if self._renewing:
                self._renewing = False  # planned token renewal: reconnect right away
                continue
            if got_data or self._session_had_data:
                # A working session ended (e.g. server drop): reconnect quickly without
                # declaring an outage; the staleness check covers a failed reconnect.
                backoff = self.BACKOFF_MIN
            else:
                self._set_connected(False, self._last_error)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.BACKOFF_MAX)

    def _set_connected(self, connected: bool, reason: str | None = None) -> None:
        """Track state and log once per outage / recovery."""
        if connected and not self.connected:
            if self._outage_logged:
                _LOGGER.info("SE-Monitor realtime feed for site %s restored", self._site_id)
            self._outage_logged = False
        elif not connected and not self._outage_logged:
            _LOGGER.warning(
                "SE-Monitor realtime feed for site %s unavailable (%s); will keep retrying",
                self._site_id,
                reason or "no data",
            )
            self._outage_logged = True
        self.connected = connected

    async def _connect_once(self) -> bool:
        """Run one websocket session. Returns True if it delivered realtime data."""
        self._session_had_data = False
        self._last_error = "no data from monitor"
        token = await self._auth.async_get_id_token()
        renew_at = _jwt_exp(token) - TOKEN_REFRESH_MARGIN if _jwt_exp(token) else None
        try:
            async with self._session.ws_connect(
                self.url,
                params={"token": token},
                heartbeat=30,
                timeout=aiohttp.ClientWSTimeout(ws_close=10),
            ) as ws:
                return await self._session_loop(ws, renew_at)
        except aiohttp.WSServerHandshakeError as err:
            if err.status in (401, 403):
                self._auth.invalidate()  # next attempt refreshes; refresh raises if login is dead
            raise

    async def _session_loop(self, ws: aiohttp.ClientWebSocketResponse, renew_at: float | None) -> bool:
        """Subscribe and consume frames until the session should end."""
        await ws.send_json({"action": "subscribeSiteLiveData", "clipsal_solar_id": self._site_id})
        # The server keeps sending status/info messages while the monitor is offline, so
        # measure silence from the last realtime frame, not from the last message.
        last_frame = time.monotonic()
        while True:
            if renew_at and time.time() >= renew_at:
                _LOGGER.debug("Reconnecting realtime websocket to renew token")
                self._renewing = True
                return self._session_had_data
            remaining = self.NO_DATA_TIMEOUT - (time.monotonic() - last_frame)
            if remaining <= 0:
                raise SEMonitorConnectionError(f"no realtime data for {self.NO_DATA_TIMEOUT:g} s")
            wait = remaining
            if renew_at:
                wait = max(min(wait, renew_at - time.time()), 0.5)
            try:
                msg = await ws.receive(timeout=wait)
            except TimeoutError:
                continue  # loop re-checks the renewal and no-data deadlines
            if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                self._last_error = "server closed the connection"
                return self._session_had_data
            if msg.type == aiohttp.WSMsgType.ERROR:
                raise SEMonitorConnectionError(f"websocket error: {ws.exception()}")
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except ValueError:
                continue
            mtype = data.get("type")
            if mtype == "realtime_update":
                last_frame = time.monotonic()
                self._session_had_data = True
                self.last_data = RealtimeData.from_payload(data.get("payload") or {})
                self.first_data.set()
                self._set_connected(True)
                self._on_data(self.last_data)
            elif mtype in ("error", "unauthorized"):
                raise SEMonitorConnectionError(f"realtime error: {data.get('payload')}")
            else:
                _LOGGER.debug(
                    "SE-Monitor ws %s: %s", mtype, (data.get("payload") or {}).get("message")
                )
