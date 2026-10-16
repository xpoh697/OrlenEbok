"""Асинхронный клиент ORLEN eBOK / PGNiG CRM API v3.0."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import logging
from typing import Any

import aiohttp

from .const import BASE_URL

_LOGGER = logging.getLogger(__name__)


class OrlenEbokAuthError(Exception):
    """Ошибка авторизации Orlen eBOK."""


class OrlenEbokApiError(Exception):
    """Ошибка взаимодействия с API Orlen eBOK."""


class OrlenEbokApiClient:
    """Асинхронный клиент API личного кабинета Orlen / PGNiG."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
    ) -> None:
        """Инициализация клиента."""
        self._session = session
        self._username = username
        self._password = password
        self._cached_token: str | None = None
        self._device_id: str = hashlib.sha256(username.encode("utf-8")).hexdigest()[:32]
        self._lock = asyncio.Lock()

    @property
    def cached_token(self) -> str | None:
        """Получить текущий кэшированный токен."""
        return self._cached_token

    def _headers(self, token: str | None = None) -> dict[str, str]:
        """Формирование заголовков запроса."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) ORLEN_mBOK/4.0.10",
        }
        if token:
            headers["AuthToken"] = token
        return headers

    async def login_api(self) -> str:
        """Прямой вход в систему через /auth/login."""
        url = f"{BASE_URL}/auth/login?api-version=3.0"
        payload = {
            "identificator": self._username,
            "accessPin": self._password,
            "rememberLogin": False,
            "DeviceId": self._device_id,
            "DeviceName": "Home Assistant",
            "DeviceType": "Android",
        }
        try:
            async with self._session.post(
                url, json=payload, headers=self._headers(), timeout=30
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise OrlenEbokAuthError(
                        f"Login failed (HTTP {resp.status}): {text[:200]}"
                    )
                data = await resp.json()
                token = data.get("Token")
                if not token:
                    raise OrlenEbokAuthError(
                        f"Auth response missing 'Token': {data}"
                    )
                self._cached_token = token
                _LOGGER.debug("Orlen eBOK authentication successful")
                return token
        except asyncio.TimeoutError as err:
            raise OrlenEbokApiError("Timeout during authentication") from err
        except aiohttp.ClientError as err:
            raise OrlenEbokApiError(f"Network error during authentication: {err}") from err

    async def get_token(self) -> str:
        """Получить действующий токен авторизации."""
        async with self._lock:
            if self._cached_token:
                return self._cached_token
            return await self.login_api()

    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Выполнение HTTP запроса с автоматическим повтором при 401."""
        url = f"{BASE_URL}{path}"
        if params is None:
            params = {}
        if "api-version" not in params:
            params["api-version"] = "3.0"

        for attempt in range(2):
            token = await self.get_token()
            headers = self._headers(token)
            try:
                async with self._session.request(
                    method,
                    url,
                    json=json_data,
                    params=params,
                    headers=headers,
                    timeout=30,
                ) as resp:
                    if resp.status == 401 and attempt == 0:
                        _LOGGER.debug("Token expired (401), re-authenticating...")
                        self._cached_token = None
                        continue
                    if resp.status != 200:
                        text = await resp.text()
                        raise OrlenEbokApiError(
                            f"API call {path} failed (HTTP {resp.status}): {text[:200]}"
                        )
                    return await resp.json()
            except asyncio.TimeoutError as err:
                raise OrlenEbokApiError(f"Timeout calling {path}") from err
            except aiohttp.ClientError as err:
                raise OrlenEbokApiError(f"Network error calling {path}: {err}") from err

        raise OrlenEbokAuthError("Authentication expired and re-login failed")

    async def get_ppg_list(self) -> list[dict[str, Any]]:
        """Получить список подключений/счетчиков (PPG)."""
        data = await self._request("GET", "/crm/get-ppg-list")
        return data.get("PpgList", [])

    async def get_all_readings_for_meter(
        self, ppg_id: str, page_size: int = 10
    ) -> list[dict[str, Any]]:
        """Получить историю показаний для указанного счетчика."""
        params = {
            "pageSize": str(page_size),
            "pageNumber": "1",
            "idPpg": str(ppg_id),
        }
        data = await self._request(
            "GET", "/crm/get-all-ppg-readings-for-meter", params=params
        )
        return data.get("MeterReadings", [])

    async def get_reading_dates(self, ppg_id: str) -> list[str]:
        """Получить допустимые даты расчетных окон подачи показаний."""
        params = {"idPpg": str(ppg_id)}
        data = await self._request("GET", "/crm/get-ppg-readings-dates", params=params)
        return data.get("MeterReadingsDates", [])

    async def validate_reading(
        self, ppg_id: str, value: int, consent_reset: bool = False
    ) -> dict[str, Any]:
        """Предварительная валидация показания перед фиксацией."""
        payload = {
            "IdPP": str(ppg_id),
            "Value": int(value),
            "ConsentMeterReset": bool(consent_reset),
        }
        data = await self._request(
            "POST", "/crm/validate-ppg-reading", json_data=payload
        )
        code = data.get("Code", -1)
        if code != 0:
            msg = data.get("Message") or f"Validation rejected with code {code}"
            raise OrlenEbokApiError(f"Reading validation failed (Code {code}): {msg}")
        return data

    async def submit_reading(
        self,
        ppg_id: str,
        value: int,
        date_str: str | None = None,
        consent_reset: bool = False,
    ) -> dict[str, Any]:
        """Подача показания счетчика газа в CRM биллинг Orlen."""
        if date_str is None:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # 1. Валидация показания перед фиксацией
        await self.validate_reading(ppg_id, value, consent_reset)

        # 2. Фиксация показания
        payload = {
            "DateReading": date_str,
            "OsdNumber": str(ppg_id),
            "Value": int(value),
            "ConsentMeterReset": bool(consent_reset),
            "UseDate": True,
            "SourceFromWWW": True,
        }
        data = await self._request(
            "POST", "/crm/add-ppg-reading-v2", json_data=payload
        )
        code = data.get("Code", -1)
        if code != 0:
            msg = data.get("Message") or f"Submission rejected with code {code}"
            raise OrlenEbokApiError(f"Submission failed (Code {code}): {msg}")
        return data

    async def cancel_reading(self, meter_number: str) -> dict[str, Any]:
        """Отмена ранее поданного показания."""
        payload = {"NumberOSD": str(meter_number)}
        data = await self._request("POST", "/crm/cancel-reading", json_data=payload)
        code = data.get("Code", -1)
        if code != 0:
            msg = data.get("Message") or f"Cancellation rejected with code {code}"
            raise OrlenEbokApiError(f"Cancel reading failed (Code {code}): {msg}")
        return data
