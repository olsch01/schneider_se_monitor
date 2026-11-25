"""Config flow for Schneider SE-Monitor."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

from .api import (
    LoginRequest,
    SEMonitorAuth,
    SEMonitorAuthError,
    SEMonitorClient,
    SEMonitorConnectionError,
    Site,
    create_login_request,
    exchange_code,
    parse_redirect_url,
)
from .const import CONF_REDIRECT_URL, CONF_REFRESH_TOKEN, CONF_SITE_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)

URL_SCHEMA = vol.Schema({vol.Required(CONF_REDIRECT_URL): str})


class SEMonitorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SE-Monitor."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._login: LoginRequest | None = None
        self._refresh_token: str | None = None
        self._sites: list[Site] = []

    async def _async_login(self, user_input: dict[str, Any]) -> dict[str, str]:
        """Exchange the pasted URL for tokens and load sites. Returns form errors."""
        assert self._login is not None
        url = user_input[CONF_REDIRECT_URL]
        if "code=" not in url and "error=" not in url:
            return {"base": "invalid_url"}
        session = async_get_clientsession(self.hass)
        try:
            code = parse_redirect_url(url, self._login)
            tokens = await exchange_code(session, code, self._login)
            auth = SEMonitorAuth(session, tokens["refresh_token"], tokens.get("id_token"))
            self._sites = await SEMonitorClient(session, auth).async_get_sites()
        except SEMonitorAuthError as err:
            _LOGGER.debug("SE-Monitor login failed: %s", err)
            self._login = None  # codes and PKCE verifiers are single-use
            return {"base": "invalid_auth"}
        except SEMonitorConnectionError:
            return {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Unexpected error during SE-Monitor login")
            return {"base": "unknown"}
        self._refresh_token = auth.refresh_token
        return {}

    def _show_login_form(self, step_id: str, errors: dict[str, str]) -> ConfigFlowResult:
        if self._login is None:
            self._login = create_login_request()
        return self.async_show_form(
            step_id=step_id,
            data_schema=URL_SCHEMA,
            errors=errors,
            description_placeholders={"login_url": self._login.url},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Browser login step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_login(user_input)
            if not errors:
                if not self._sites:
                    return self.async_abort(reason="no_sites")
                if len(self._sites) == 1:
                    return await self._async_create(self._sites[0])
                return await self.async_step_site()
        return self._show_login_form("user", errors)

    async def async_step_site(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a site when the account has more than one."""
        if user_input is not None:
            site = next(s for s in self._sites if str(s.site_id) == user_input[CONF_SITE_ID])
            return await self._async_create(site)
        options = [SelectOptionDict(value=str(s.site_id), label=s.name) for s in self._sites]
        return self.async_show_form(
            step_id="site",
            data_schema=vol.Schema(
                {vol.Required(CONF_SITE_ID): SelectSelector(SelectSelectorConfig(options=options))}
            ),
        )

    async def _async_create(self, site: Site) -> ConfigFlowResult:
        await self.async_set_unique_id(str(site.site_id))
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=site.name,
            data={CONF_REFRESH_TOKEN: self._refresh_token, CONF_SITE_ID: site.site_id},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauth when the refresh token stops working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Repeat the browser login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_login(user_input)
            if not errors:
                entry = self._get_reauth_entry()
                if not any(s.site_id == entry.data[CONF_SITE_ID] for s in self._sites):
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_REFRESH_TOKEN: self._refresh_token}
                )
        return self._show_login_form("reauth_confirm", errors)
