"""Config and options flows for Omada Guest Access."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig

from .const import (
    CONF_ALLOWED_NETWORKS,
    CONF_CONTROLLER_ID,
    CONF_CONTROLLER_URL,
    CONF_DEFAULT_DURATION,
    CONF_DURATION_OPTIONS,
    CONF_ENABLE_REVOKE,
    CONF_PASSWORD,
    CONF_PENDING_TIMEOUT,
    CONF_PORTAL_ACCENT,
    CONF_PORTAL_CSS,
    CONF_PORTAL_FOOTER,
    CONF_PORTAL_HEADER,
    CONF_PORTAL_MESSAGE,
    CONF_PORTAL_PORT,
    CONF_PORTAL_TEMPLATE,
    CONF_PORTAL_TITLE,
    CONF_PORTAL_URL,
    CONF_REQUIRE_TERMS,
    CONF_RETENTION_DAYS,
    CONF_SITE,
    CONF_TERMS_TEXT,
    CONF_TRUSTED_PROXIES,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_DURATION_HOURS,
    DEFAULT_DURATION_OPTIONS,
    DEFAULT_PENDING_TIMEOUT_MINUTES,
    DEFAULT_PORTAL_ACCENT,
    DEFAULT_PORTAL_MESSAGE,
    DEFAULT_PORTAL_PORT,
    DEFAULT_PORTAL_TITLE,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)
from .omada_client import (
    OmadaApiError,
    OmadaAuthError,
    OmadaExternalPortalClient,
    OmadaTlsError,
    normalize_controller_url,
)
from .portal import _safe_redirect_url, parse_networks
from .portal_render import PortalTemplateError, validate_template
from .settings import parse_duration_options


class OmadaGuestAccessConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure the documented Omada External Portal API."""

    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self._async_configure("user", user_input)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self._async_configure("reauth_confirm", user_input, self._get_reauth_entry())

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self._async_configure("reconfigure", user_input, self._get_reconfigure_entry())

    async def _async_configure(self, step: str, user_input: dict[str, Any] | None, entry=None) -> FlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        values = dict(entry.data) | dict(entry.options) if entry else {}
        if user_input is not None:
            values.update(user_input)
            client = None
            try:
                values[CONF_CONTROLLER_URL], values[CONF_CONTROLLER_ID] = normalize_controller_url(
                    values[CONF_CONTROLLER_URL], values.get(CONF_CONTROLLER_ID, "")
                )
                await self.hass.async_add_executor_job(validate_portal_settings, values)
                if port_in_use(self.hass, values[CONF_PORTAL_PORT], entry):
                    errors["portal_port"] = "port_in_use"
                else:
                    client = OmadaExternalPortalClient(self.hass, values)
                    await client.async_test_connection()
            except OmadaAuthError as err:
                errors["base"] = "invalid_auth"
                placeholders["error_detail"] = str(err)
            except OmadaTlsError:
                errors["base"] = "tls_error"
            except OmadaApiError as err:
                errors["base"] = "cannot_connect"
                placeholders["error_detail"] = str(err)
            except PortalTemplateError as err:
                errors["base"] = "invalid_template"
                placeholders["error_detail"] = str(err)
            except ValueError:
                errors["base"] = "invalid_config"
            finally:
                if client is not None:
                    await client.async_close()
            if not errors:
                identity = f"{values[CONF_CONTROLLER_URL]}/{values[CONF_CONTROLLER_ID]}:{values[CONF_SITE]}"
                if any(
                    other.entry_id != (entry.entry_id if entry else None) and _identity(other) == identity
                    for other in self.hass.config_entries.async_entries(DOMAIN)
                ):
                    return self.async_abort(reason="already_configured")
                await self.async_set_unique_id(identity)
                if entry:
                    return self.async_update_reload_and_abort(
                        entry,
                        unique_id=identity,
                        data=values,
                        options={},
                        reason="reauth_successful" if step == "reauth_confirm" else "reconfigure_successful",
                    )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=f"Omada Guest Access ({values[CONF_SITE]})", data=values)
        return self.async_show_form(
            step_id=step, data_schema=_data_schema(values), errors=errors, description_placeholders=placeholders
        )

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> OmadaGuestAccessOptionsFlow:
        return OmadaGuestAccessOptionsFlow()


class OmadaGuestAccessOptionsFlow(config_entries.OptionsFlow):
    """The base class owns config_entry; never assign its read-only property."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        values = dict(self.config_entry.data) | dict(self.config_entry.options)
        errors = {}
        placeholders = {}
        if user_input is not None:
            values.update(user_input)
            try:
                await self.hass.async_add_executor_job(validate_portal_settings, values)
            except PortalTemplateError as err:
                errors["base"] = "invalid_template"
                placeholders["error_detail"] = str(err)
            except ValueError:
                errors["base"] = "invalid_config"
            if port_in_use(self.hass, values[CONF_PORTAL_PORT], self.config_entry):
                errors["portal_port"] = "port_in_use"
            if not errors:
                return self.async_create_entry(title="", data=user_input)
        fields = _data_schema(values).schema
        keys = {
            CONF_DURATION_OPTIONS,
            CONF_ENABLE_REVOKE,
            CONF_PORTAL_HEADER,
            CONF_PORTAL_FOOTER,
            CONF_PORTAL_CSS,
            CONF_PORTAL_TEMPLATE,
            CONF_PORTAL_URL,
            CONF_PORTAL_PORT,
            CONF_DEFAULT_DURATION,
            CONF_PENDING_TIMEOUT,
            CONF_RETENTION_DAYS,
            CONF_TRUSTED_PROXIES,
            CONF_ALLOWED_NETWORKS,
            CONF_PORTAL_TITLE,
            CONF_PORTAL_MESSAGE,
            CONF_PORTAL_ACCENT,
            CONF_TERMS_TEXT,
            CONF_REQUIRE_TERMS,
        }
        schema = vol.Schema({key: value for key, value in fields.items() if key.schema in keys})
        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors, description_placeholders=placeholders
        )


def validate_portal_settings(values: dict[str, Any]) -> None:
    parse_duration_options(values.get(CONF_DURATION_OPTIONS, DEFAULT_DURATION_OPTIONS))
    if not isinstance(values.get(CONF_ENABLE_REVOKE, True), bool):
        raise ValueError("Enable revoke must be a boolean")
    for key, default, maximum in (
        (CONF_PORTAL_TITLE, DEFAULT_PORTAL_TITLE, 80),
        (CONF_PORTAL_MESSAGE, DEFAULT_PORTAL_MESSAGE, 500),
        (CONF_TERMS_TEXT, "", 4000),
        (CONF_PORTAL_HEADER, "", 10000),
        (CONF_PORTAL_FOOTER, "", 10000),
        (CONF_PORTAL_CSS, "", 20000),
        (CONF_PORTAL_TEMPLATE, "", 50000),
    ):
        value = values.get(key, default)
        if not isinstance(value, str) or len(value) > maximum:
            raise ValueError("Portal text is too long or invalid")
    if not values.get(CONF_PORTAL_TITLE, DEFAULT_PORTAL_TITLE).strip():
        raise ValueError("Portal title must not be blank")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", values.get(CONF_PORTAL_ACCENT, DEFAULT_PORTAL_ACCENT)):
        raise ValueError("Accent must be a six-digit hex color")
    if not isinstance(values.get(CONF_REQUIRE_TERMS, False), bool):
        raise ValueError("Require terms must be a boolean")
    if values.get(CONF_REQUIRE_TERMS) and not values.get(CONF_TERMS_TEXT, "").strip():
        raise ValueError("Terms text is required when acceptance is mandatory")
    validate_template(values)
    parse_networks(values.get(CONF_TRUSTED_PROXIES, ""))
    parse_networks(values.get(CONF_ALLOWED_NETWORKS, ""))
    url = values.get(CONF_PORTAL_URL, "")
    if url:
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
        if not _safe_redirect_url(url) or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("The public portal URL must be an HTTP(S) origin without a subpath")


def port_in_use(hass, port: int, entry=None) -> bool:
    return any(
        other.entry_id != (entry.entry_id if entry else None)
        and (dict(other.data) | dict(other.options)).get(CONF_PORTAL_PORT, DEFAULT_PORTAL_PORT) == port
        for other in hass.config_entries.async_entries(DOMAIN)
    )


def _identity(entry) -> str | None:
    try:
        origin, identifier = normalize_controller_url(
            entry.data[CONF_CONTROLLER_URL], entry.data.get(CONF_CONTROLLER_ID, "")
        )
        return f"{origin}/{identifier}:{entry.data[CONF_SITE]}"
    except (KeyError, ValueError):
        return None


def _data_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Optional(
                CONF_DURATION_OPTIONS, default=defaults.get(CONF_DURATION_OPTIONS, DEFAULT_DURATION_OPTIONS)
            ): cv.string,
            vol.Optional(CONF_ENABLE_REVOKE, default=defaults.get(CONF_ENABLE_REVOKE, True)): cv.boolean,
            **{
                vol.Optional(key, default=defaults.get(key, "")): TextSelector(TextSelectorConfig(multiline=True))
                for key in (CONF_PORTAL_HEADER, CONF_PORTAL_FOOTER, CONF_PORTAL_CSS, CONF_PORTAL_TEMPLATE)
            },
            vol.Optional(CONF_PORTAL_TITLE, default=defaults.get(CONF_PORTAL_TITLE, DEFAULT_PORTAL_TITLE)): vol.All(
                cv.string, vol.Length(min=1, max=80)
            ),
            vol.Optional(
                CONF_PORTAL_MESSAGE, default=defaults.get(CONF_PORTAL_MESSAGE, DEFAULT_PORTAL_MESSAGE)
            ): TextSelector(TextSelectorConfig(multiline=True)),
            vol.Optional(
                CONF_PORTAL_ACCENT, default=defaults.get(CONF_PORTAL_ACCENT, DEFAULT_PORTAL_ACCENT)
            ): cv.string,
            vol.Optional(CONF_TERMS_TEXT, default=defaults.get(CONF_TERMS_TEXT, "")): TextSelector(
                TextSelectorConfig(multiline=True)
            ),
            vol.Optional(CONF_REQUIRE_TERMS, default=defaults.get(CONF_REQUIRE_TERMS, False)): cv.boolean,
            # cv.url cannot be serialized for the HA frontend. URL validation is
            # performed by normalize_controller_url when the form is submitted.
            vol.Required(CONF_CONTROLLER_URL, default=defaults.get(CONF_CONTROLLER_URL, "")): cv.string,
            vol.Optional(CONF_CONTROLLER_ID, default=defaults.get(CONF_CONTROLLER_ID, "")): cv.string,
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): vol.All(cv.string, vol.Length(min=1)),
            vol.Required(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")): cv.string,
            vol.Required(CONF_SITE, default=defaults.get(CONF_SITE, "Default")): vol.All(cv.string, vol.Length(min=1)),
            vol.Optional(CONF_TRUSTED_PROXIES, default=defaults.get(CONF_TRUSTED_PROXIES, "")): cv.string,
            vol.Optional(CONF_ALLOWED_NETWORKS, default=defaults.get(CONF_ALLOWED_NETWORKS, "")): cv.string,
            vol.Optional(CONF_PORTAL_URL, default=defaults.get(CONF_PORTAL_URL, "")): cv.string,
            vol.Required(CONF_PORTAL_PORT, default=defaults.get(CONF_PORTAL_PORT, DEFAULT_PORTAL_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Required(CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)): cv.boolean,
            vol.Required(
                CONF_DEFAULT_DURATION, default=defaults.get(CONF_DEFAULT_DURATION, DEFAULT_DURATION_HOURS)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=720)),
            vol.Required(
                CONF_PENDING_TIMEOUT, default=defaults.get(CONF_PENDING_TIMEOUT, DEFAULT_PENDING_TIMEOUT_MINUTES)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
            vol.Required(
                CONF_RETENTION_DAYS, default=defaults.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
        }
    )
