"""Сенсоры показаний и статуса ORLEN eBOK."""
from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_ALLOWED_DATES,
    ATTR_LAST_SUBMISSION_DATE,
    ATTR_LAST_SUBMISSION_RESULT,
    ATTR_METER_NUMBER,
    ATTR_PPG_ID,
    ATTR_READING_TYPE,
    ATTR_TARIFF,
    DOMAIN,
)
from .coordinator import OrlenEbokCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Настройка сенсоров ORLEN eBOK на основе ConfigEntry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: OrlenEbokCoordinator = data["coordinator"]

    entities: list[SensorEntity] = []

    for ppg_id in coordinator.data:
        entities.extend(
            [
                OrlenLastReadingSensor(coordinator, entry, ppg_id),
                OrlenLastReadingDateSensor(coordinator, entry, ppg_id),
                OrlenSubmissionStatusSensor(coordinator, entry, ppg_id),
            ]
        )

    async_add_entities(entities)


class OrlenBaseEntity(CoordinatorEntity[OrlenEbokCoordinator]):
    """Базовый класс для сущностей счетчика газа Orlen eBOK."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OrlenEbokCoordinator,
        entry: ConfigEntry,
        ppg_id: str,
        sensor_type: str,
    ) -> None:
        """Инициализация базовой сущности."""
        super().__init__(coordinator)
        self.entry = entry
        self.ppg_id = ppg_id
        self._attr_unique_id = f"{entry.entry_id}_{ppg_id}_{sensor_type}"

    @property
    def device_info(self) -> DeviceInfo:
        """Информация об устройстве счетчика газа."""
        device_data = self.coordinator.data.get(self.ppg_id, {})
        meter_number = device_data.get("meter_number", self.ppg_id)
        ppg_info = device_data.get("ppg", {})
        tariff = ppg_info.get("Tariff", "PGNiG")

        return DeviceInfo(
            identifiers={(DOMAIN, self.ppg_id)},
            name=f"Licznik gazu {meter_number}",
            manufacturer="ORLEN / PGNiG",
            model=f"Taryfa {tariff}",
            serial_number=meter_number,
        )


class OrlenLastReadingSensor(OrlenBaseEntity, SensorEntity):
    """Сенсор: Последнее зафиксированное показание счетчика газа."""

    _attr_device_class = SensorDeviceClass.GAS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_icon = "mdi:counter"
    _attr_translation_key = "last_reading"

    def __init__(
        self, coordinator: OrlenEbokCoordinator, entry: ConfigEntry, ppg_id: str
    ) -> None:
        super().__init__(coordinator, entry, ppg_id, "last_reading")
        self._attr_name = "Последнее показание"

    @property
    def native_value(self) -> float | None:
        """Текущее показание в кубических метрах."""
        device_data = self.coordinator.data.get(self.ppg_id, {})
        last_reading = device_data.get("last_reading", {})
        val = last_reading.get("Value")
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                return None
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Дополнительные атрибуты показания."""
        device_data = self.coordinator.data.get(self.ppg_id, {})
        last_reading = device_data.get("last_reading", {})
        ppg_info = device_data.get("ppg", {})

        return {
            ATTR_PPG_ID: self.ppg_id,
            ATTR_METER_NUMBER: device_data.get("meter_number"),
            ATTR_TARIFF: ppg_info.get("Tariff"),
            ATTR_READING_TYPE: last_reading.get("Type"),
        }


class OrlenLastReadingDateSensor(OrlenBaseEntity, SensorEntity):
    """Сенсор: Дата последнего показания."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:calendar-clock"
    _attr_translation_key = "last_reading_date"

    def __init__(
        self, coordinator: OrlenEbokCoordinator, entry: ConfigEntry, ppg_id: str
    ) -> None:
        super().__init__(coordinator, entry, ppg_id, "last_reading_date")
        self._attr_name = "Дата последнего показания"

    @property
    def native_value(self) -> datetime | None:
        """Дата последнего снятого показания в формате timestamp."""
        device_data = self.coordinator.data.get(self.ppg_id, {})
        last_reading = device_data.get("last_reading", {})
        raw_date = last_reading.get("ReadingDateUtc") or last_reading.get("ReadingDateLocal")
        if raw_date:
            try:
                # Парсинг ISO формата даты
                parsed = dt_util.parse_datetime(str(raw_date))
                return parsed
            except Exception:
                return None
        return None


class OrlenSubmissionStatusSensor(OrlenBaseEntity, SensorEntity):
    """Сенсор: Статус передачи показания."""

    _attr_icon = "mdi:cloud-upload-outline"
    _attr_translation_key = "reading_submission_status"

    def __init__(
        self, coordinator: OrlenEbokCoordinator, entry: ConfigEntry, ppg_id: str
    ) -> None:
        super().__init__(coordinator, entry, ppg_id, "submission_status")
        self._attr_name = "Статус передачи показаний"

    @property
    def native_value(self) -> str:
        """Текущий статус (allowed, already_submitted, disallowed, submitted)."""
        device_data = self.coordinator.data.get(self.ppg_id, {})
        return str(device_data.get("status", "unknown"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Атрибуты доступных окон и истории отправки."""
        device_data = self.coordinator.data.get(self.ppg_id, {})
        last_submission = device_data.get("last_submission")

        attrs: dict[str, Any] = {
            ATTR_PPG_ID: self.ppg_id,
            ATTR_METER_NUMBER: device_data.get("meter_number"),
            ATTR_ALLOWED_DATES: device_data.get("reading_dates", []),
        }

        if last_submission:
            attrs[ATTR_LAST_SUBMISSION_DATE] = last_submission.get("timestamp")
            attrs[ATTR_LAST_SUBMISSION_RESULT] = last_submission.get("result")

        return attrs
