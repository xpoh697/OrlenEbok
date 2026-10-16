"""Кнопки управления ORLEN eBOK."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import OrlenEbokApiError
from .const import (
    DOMAIN,
    EVENT_READING_CANCELLED,
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
        entities.append(OrlenCancelReadingButton(coordinator, entry, ppg_id))

    async_add_entities(entities)


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
