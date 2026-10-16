"""Инициализация интеграции ORLEN eBOK."""
from __future__ import annotations

import logging
import math
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import OrlenEbokApiClient, OrlenEbokApiError
from .const import (
    ATTR_CONSENT_METER_RESET,
    ATTR_DATE,
    ATTR_PPG_ID,
    ATTR_VALUE,
    CONF_PASSWORD,
    CONF_SOURCE_SENSOR,
    CONF_USERNAME,
    DOMAIN,
    EVENT_READING_CANCELLED,
    EVENT_READING_SUBMITTED,
    SERVICE_CANCEL_READING,
    SERVICE_SUBMIT_READING,
    STATUS_ALLOWED,
)
from .coordinator import OrlenEbokCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Настройка интеграции ORLEN eBOK из ConfigEntry."""
    session = async_get_clientsession(hass)
    api = OrlenEbokApiClient(
        session=session,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
    )

    coordinator = OrlenEbokCoordinator(hass, api)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "api": api,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Слушатель изменений параметров в OptionsFlow для автоматической перезагрузки
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    async def handle_submit_reading(call: ServiceCall) -> None:
        """Служба подачи показаний счетчика газа (вызывается из автоматизаций)."""
        data = coordinator.data
        if not data:
            raise HomeAssistantError("No gas meters found in Orlen eBOK account")

        ppg_id = call.data.get(ATTR_PPG_ID)
        raw_val = call.data.get(ATTR_VALUE)
        date_str = call.data.get(ATTR_DATE)
        consent = bool(call.data.get(ATTR_CONSENT_METER_RESET, False))

        # Определение точки учета (PPG)
        if not ppg_id:
            if len(data) == 1:
                ppg_id = next(iter(data.keys()))
            else:
                available = ", ".join(data.keys())
                raise HomeAssistantError(
                    f"Multiple meters found. Please specify ppg_id in service data (Available: {available})"
                )

        if ppg_id not in data:
            raise HomeAssistantError(f"Gas meter with PPG ID '{ppg_id}' not found")

        device_data = data[ppg_id]
        last_reading = device_data.get("last_reading", {})
        status = device_data.get("status")

        # Если значение не передано, пробуем получить из привязанного source_sensor
        if raw_val is None:
            source_sensor = entry.options.get(CONF_SOURCE_SENSOR) or entry.data.get(
                CONF_SOURCE_SENSOR
            )
            if not source_sensor:
                raise HomeAssistantError(
                    "Parameter 'value' is required or 'source_sensor' must be configured in settings"
                )
            state = hass.states.get(source_sensor)
            if not state or state.state in ("unknown", "unavailable"):
                raise HomeAssistantError(
                    f"Source sensor '{source_sensor}' state is unavailable ({state.state if state else 'None'})"
                )
            raw_val = state.state

        # Санитаризация входного значения
        try:
            cleaned = str(raw_val).strip().replace(",", ".")
            parsed_float = float(cleaned)
            val_int = int(math.floor(parsed_float))
            if val_int < 0:
                raise ValueError("Reading value cannot be negative")
        except (ValueError, TypeError) as err:
            raise HomeAssistantError(
                f"Invalid meter reading value: '{raw_val}'. Must be a valid positive number."
            ) from err

        # Проверка монотонного возрастания показания
        last_val = last_reading.get("Value")
        if last_val is not None and not consent:
            try:
                if val_int < int(float(last_val)):
                    raise HomeAssistantError(
                        f"New reading ({val_int} m³) cannot be lower than previous registered reading ({last_val} m³)"
                    )
            except (ValueError, TypeError):
                pass

        # Проверка открытого окна подачи
        if status != STATUS_ALLOWED and not consent:
            raise HomeAssistantError(
                f"Reading submission is not allowed at this moment (Current status: {status})"
            )

        _LOGGER.info("Submitting gas meter reading %s m³ for PPG %s", val_int, ppg_id)
        try:
            result = await api.submit_reading(
                ppg_id, val_int, date_str=date_str, consent_reset=consent
            )
        except OrlenEbokApiError as err:
            raise HomeAssistantError(f"Submission failed: {err}") from err

        # Оптимистичное обновление данных в памяти
        coordinator.async_record_submission(ppg_id, val_int, date_str or "", result)

        hass.bus.async_fire(
            EVENT_READING_SUBMITTED,
            {
                "ppg_id": ppg_id,
                "value": val_int,
                "date": date_str,
                "result": result,
            },
        )

    async def handle_cancel_reading(call: ServiceCall) -> None:
        """Служба отмены ранее поданного показания."""
        data = coordinator.data
        if not data:
            raise HomeAssistantError("No gas meters found")

        ppg_id = call.data.get(ATTR_PPG_ID)
        if not ppg_id:
            if len(data) == 1:
                ppg_id = next(iter(data.keys()))
            else:
                available = ", ".join(data.keys())
                raise HomeAssistantError(
                    f"Multiple meters found. Please specify ppg_id (Available: {available})"
                )

        if ppg_id not in data:
            raise HomeAssistantError(f"Gas meter with PPG ID '{ppg_id}' not found")

        meter_number = data[ppg_id].get("meter_number", ppg_id)
        _LOGGER.info(
            "Cancelling meter reading for meter %s (PPG %s)", meter_number, ppg_id
        )
        try:
            result = await api.cancel_reading(meter_number)
        except OrlenEbokApiError as err:
            raise HomeAssistantError(f"Cancel reading failed: {err}") from err

        # Сброс локального оптимистичного состояния при отмене
        coordinator.optimistic_submissions.pop(ppg_id, None)

        hass.bus.async_fire(
            EVENT_READING_CANCELLED,
            {
                "ppg_id": ppg_id,
                "meter_number": meter_number,
                "result": result,
            },
        )
        await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_SUBMIT_READING, handle_submit_reading)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL_READING, handle_cancel_reading)

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Перезагрузка интеграции при изменении настроек."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Выгрузка интеграции."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
