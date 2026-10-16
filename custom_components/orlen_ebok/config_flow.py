"""Config Flow для настройки интеграции ORLEN eBOK."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import OrlenEbokApiClient, OrlenEbokApiError, OrlenEbokAuthError
from .const import (
    CONF_PASSWORD,
    CONF_SOURCE_SENSOR,
    CONF_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_SOURCE_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
    }
)


class OrlenEbokConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Поток конфигурации интеграции ORLEN eBOK."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Шаг ввода логина, пароля и привязки датчика газа."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD].strip()
            source_sensor = (user_input.get(CONF_SOURCE_SENSOR) or "").strip()

            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass)
            api = OrlenEbokApiClient(
                session=session,
                username=username,
                password=password,
            )

            try:
                await api.login_api()
            except OrlenEbokAuthError:
                errors["base"] = "invalid_auth"
            except OrlenEbokApiError:
                errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Unexpected error during login: %s", err)
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"ORLEN eBOK ({username})",
                    data={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                        CONF_SOURCE_SENSOR: source_sensor,
                    },
                    options={
                        CONF_SOURCE_SENSOR: source_sensor,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Создание потока дополнительных параметров (OptionsFlow)."""
        return OrlenEbokOptionsFlow()


class OrlenEbokOptionsFlow(config_entries.OptionsFlow):
    """Поток опций интеграции ORLEN eBOK."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Управление опциями (выбор локального датчика газа)."""
        if user_input is not None:
            cleaned_sensor = (user_input.get(CONF_SOURCE_SENSOR) or "").strip()
            return self.async_create_entry(
                title="", data={CONF_SOURCE_SENSOR: cleaned_sensor}
            )

        current_sensor = (
            self.config_entry.options.get(CONF_SOURCE_SENSOR)
            or self.config_entry.data.get(CONF_SOURCE_SENSOR)
            or ""
        ).strip()

        schema_dict: dict[Any, Any] = {}
        if current_sensor:
            schema_dict[
                vol.Optional(CONF_SOURCE_SENSOR, default=current_sensor)
            ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
        else:
            schema_dict[
                vol.Optional(CONF_SOURCE_SENSOR)
            ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_dict))
