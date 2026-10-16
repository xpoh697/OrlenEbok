"""Координатор опроса данных ORLEN eBOK."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import OrlenEbokApiClient, OrlenEbokApiError, OrlenEbokAuthError
from .const import (
    DEFAULT_UPDATE_INTERVAL_HOURS,
    DOMAIN,
    STATUS_ALLOWED,
    STATUS_ALREADY_SUBMITTED,
    STATUS_DISALLOWED,
    STATUS_SUBMITTED,
)

_LOGGER = logging.getLogger(__name__)


class OrlenEbokCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Класс координатора для сбора и кэширования данных Orlen eBOK."""

    def __init__(self, hass: HomeAssistant, api: OrlenEbokApiClient) -> None:
        """Инициализация координатора."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=DEFAULT_UPDATE_INTERVAL_HOURS),
        )
        self.api = api
        self.optimistic_submissions: dict[str, dict[str, Any]] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        """Загрузка свежих данных по всем счетчикам из API Orlen."""
        try:
            ppg_list = await self.api.get_ppg_list()
            devices: dict[str, dict[str, Any]] = {}

            for ppg in ppg_list:
                ppg_id = str(ppg.get("IdPPG"))
                meter_number = str(ppg.get("MeterNumber") or ppg_id)

                # Загрузка последних показаний
                readings = []
                try:
                    readings = await self.api.get_all_readings_for_meter(
                        ppg_id, page_size=5
                    )
                except OrlenEbokApiError as err:
                    _LOGGER.warning(
                        "Failed to fetch reading history for PPG %s: %s", ppg_id, err
                    )

                last_reading = readings[0] if readings else {}

                # Загрузка допустимых дат окна подачи
                reading_dates = []
                try:
                    reading_dates = await self.api.get_reading_dates(ppg_id)
                except OrlenEbokApiError as err:
                    _LOGGER.debug(
                        "Failed to fetch reading dates for PPG %s: %s", ppg_id, err
                    )

                # Определение статуса подачи
                reading_added = bool(ppg.get("ReadingAdded"))
                add_reading_mode = ppg.get("AddReadingMode")

                optimistic = self.optimistic_submissions.get(ppg_id)
                if optimistic:
                    status = STATUS_SUBMITTED
                    # Если есть свежая оптимистичная запись, подменяем отображение
                    last_reading = {
                        **last_reading,
                        "Value": optimistic["value"],
                        "ReadingDateUtc": optimistic["date"],
                    }
                elif reading_added:
                    status = STATUS_ALREADY_SUBMITTED
                elif add_reading_mode == "AllowBeforePeriods":
                    status = STATUS_ALLOWED
                else:
                    status = STATUS_DISALLOWED

                devices[ppg_id] = {
                    "ppg": ppg,
                    "meter_number": meter_number,
                    "last_reading": last_reading,
                    "readings_history": readings,
                    "reading_dates": reading_dates,
                    "status": status,
                    "last_submission": optimistic,
                }

            return devices
        except OrlenEbokAuthError as err:
            raise UpdateFailed(f"Authentication failure: {err}") from err
        except OrlenEbokApiError as err:
            raise UpdateFailed(f"Communication error with Orlen eBOK: {err}") from err

    def async_record_submission(
        self, ppg_id: str, value: int, date_str: str, result: dict[str, Any]
    ) -> None:
        """Оптимистичная фиксация отправленного показания в памяти координатора."""
        now_iso = datetime.now(timezone.utc).isoformat()
        self.optimistic_submissions[ppg_id] = {
            "value": value,
            "date": date_str,
            "timestamp": now_iso,
            "result": result,
        }
        if self.data and ppg_id in self.data:
            self.data[ppg_id]["status"] = STATUS_SUBMITTED
            self.data[ppg_id]["last_reading"] = {
                **self.data[ppg_id].get("last_reading", {}),
                "Value": value,
                "ReadingDateUtc": date_str,
            }
            self.data[ppg_id]["last_submission"] = self.optimistic_submissions[ppg_id]
            self.async_update_listeners()
