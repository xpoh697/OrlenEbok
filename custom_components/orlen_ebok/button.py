"""Кнопки управления ORLEN eBOK."""
from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import OrlenEbokApiError
from .const import (
    CONF_SOURCE_SENSOR,
    DOMAIN,
    EVENT_READING_CANCELLED,
    EVENT_READING_SUBMITTED,
    STATUS_ALLOWED,
    STATUS_ALREADY_SUBMITTED,
    STATUS_SUBMITTED,
)
from .coordinator import OrlenEbokCoordinator
from .sensor import OrlenBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Настройка кнопок ORLEN eBOK на основе ConfigEntry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: OrlenEbokCoordinator = data["coordinator"]

    entities: list[ButtonEntity] = []

    for ppg_id in coordinator.data:
        entities.append(OrlenSubmitReadingButton(coordinator, entry, ppg_id))
        entities.append(OrlenCancelReadingButton(coordinator, entry, ppg_id))

    async_add_entities(entities)


class OrlenSubmitReadingButton(OrlenBaseEntity, ButtonEntity):
    """Кнопка для передачи текущего показания из привязанного сенсора."""

    _attr_icon = "mdi:gas-meter"
    _attr_translation_key = "submit_reading"

    def __init__(
        self, coordinator: OrlenEbokCoordinator, entry: ConfigEntry, ppg_id: str
    ) -> None:
        super().__init__(coordinator, entry, ppg_id, "submit_reading")
        self._attr_name = "Подать текущее показание"
        self._lock = asyncio.Lock()

    async def async_press(self) -> None:
        """Обработка нажатия кнопки подачи показания газа."""
        if self._lock.locked():
            _LOGGER.warning("Запрос на подачу показания уже выполняется")
            return

        async with self._lock:
            data = self.coordinator.data
            if not data or self.ppg_id not in data:
                raise HomeAssistantError(f"Точка учета с PPG ID '{self.ppg_id}' не найдена")

            device_data = data[self.ppg_id]
            status = device_data.get("status")
            last_reading = device_data.get("last_reading", {})

            # Проверка доступности окна подачи в биллинге
            if status != STATUS_ALLOWED:
                raise HomeAssistantError(
                    f"Подача показаний сейчас недоступна в биллинге Orlen (Текущий статус: {status})"
                )

            # Получение привязанного source_sensor
            source_sensor = self.entry.options.get(CONF_SOURCE_SENSOR) or self.entry.data.get(
                CONF_SOURCE_SENSOR
            )
            if not source_sensor:
                raise HomeAssistantError(
                    "Локальный сенсор газа не настроен в параметрах интеграции ORLEN eBOK. "
                    "Укажите сенсор в настройках интеграции (Настройки -> Интеграции -> ORLEN eBOK -> Настроить)."
                )

            state = self.hass.states.get(source_sensor)
            if not state or state.state in ("unknown", "unavailable"):
                raise HomeAssistantError(
                    f"Локальный сенсор газа '{source_sensor}' недоступен (состояние: {state.state if state else 'None'})"
                )

            # Санитаризация значения
            try:
                cleaned = str(state.state).strip().replace(",", ".")
                parsed_float = float(cleaned)
                val_int = int(math.floor(parsed_float))
                if val_int < 0:
                    raise ValueError("Показание не может быть отрицательным")
            except (ValueError, TypeError) as err:
                raise HomeAssistantError(
                    f"Некорректное значение датчика газа '{source_sensor}': '{state.state}'"
                ) from err

            # Проверка монотонного возрастания показания
            last_val = last_reading.get("Value")
            if last_val is not None:
                try:
                    if val_int < int(float(last_val)):
                        raise HomeAssistantError(
                            f"Новое показание ({val_int} м³) не может быть меньше предыдущего зафиксированного ({last_val} м³)"
                        )
                except (ValueError, TypeError):
                    pass

            meter_number = device_data.get("meter_number", self.ppg_id)
            _LOGGER.info(
                "Ручная подача показаний газа %s м³ для счетчика %s (PPG %s)",
                val_int,
                meter_number,
                self.ppg_id,
            )

            try:
                result = await self.coordinator.api.submit_reading(self.ppg_id, val_int)
            except OrlenEbokApiError as err:
                raise HomeAssistantError(f"Сбой отправки показания: {err}") from err

            # Оптимистичное обновление данных в памяти
            self.coordinator.async_record_submission(self.ppg_id, val_int, "", result)

            self.hass.bus.async_fire(
                EVENT_READING_SUBMITTED,
                {
                    "ppg_id": self.ppg_id,
                    "meter_number": meter_number,
                    "value": val_int,
                    "result": result,
                },
            )

            await self.coordinator.async_request_refresh()


class OrlenCancelReadingButton(OrlenBaseEntity, ButtonEntity):
    """Кнопка для безопасной отмены последнего поданного показания."""

    _attr_icon = "mdi:cancel"
    _attr_translation_key = "cancel_reading"

    def __init__(
        self, coordinator: OrlenEbokCoordinator, entry: ConfigEntry, ppg_id: str
    ) -> None:
        super().__init__(coordinator, entry, ppg_id, "cancel_reading")
        self._attr_name = "Отменить последнее показание"
        self._lock = asyncio.Lock()

    async def async_press(self) -> None:
        """Обработка нажатия кнопки отмены показания с защитой от случайного вызова."""
        if self._lock.locked():
            _LOGGER.warning("Запрос на отмену показания уже выполняется")
            return

        async with self._lock:
            data = self.coordinator.data
            if not data or self.ppg_id not in data:
                raise HomeAssistantError(f"Точка учета с PPG ID '{self.ppg_id}' не найдена")

            device_data = data[self.ppg_id]
            status = device_data.get("status")
            has_optimistic = self.ppg_id in self.coordinator.optimistic_submissions

            # Защита: отменять можно только если показание действительно подано
            if status not in (STATUS_SUBMITTED, STATUS_ALREADY_SUBMITTED) and not has_optimistic:
                raise HomeAssistantError(
                    "В текущем расчетном периоде нет поданных показаний для отмены"
                )

            meter_number = device_data.get("meter_number", self.ppg_id)
            _LOGGER.info(
                "Запрос на отмену показания газа для счетчика %s (PPG %s)",
                meter_number,
                self.ppg_id,
            )

            try:
                result = await self.coordinator.api.cancel_reading(meter_number)
            except OrlenEbokApiError as err:
                raise HomeAssistantError(f"Сбой при отмене показания: {err}") from err

            # Немедленный сброс оптимистичных данных
            self.coordinator.optimistic_submissions.pop(self.ppg_id, None)

            self.hass.bus.async_fire(
                EVENT_READING_CANCELLED,
                {
                    "ppg_id": self.ppg_id,
                    "meter_number": meter_number,
                    "result": result,
                },
            )

            await self.coordinator.async_request_refresh()
