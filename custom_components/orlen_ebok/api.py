"""Асинхронный клиент ORLEN eBOK / PGNiG CRM API v3.0."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import logging
from typing import Any

import aiohttp
from yarl import URL

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
        self._username = username.strip()
        self._password = password.strip()
        self._cached_token: str | None = None
        self._device_id: str = hashlib.sha256(self._username.encode("utf-8")).hexdigest()[:32]
        self._lock = asyncio.Lock()

    @property
    def cached_token(self) -> str | None:
        """Получить текущий кэшированный токен."""
        return self._cached_token

    def _headers(self, token: str | None = None) -> dict[str, str]:
        """Формирование заголовков запроса."""
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/",
            "Cookie": f"pgnig-ebok-device-token={self._device_id}",
        }
        if token:
            headers["AuthToken"] = token
        return headers

    async def _init_session(self) -> None:
        """Инициализация сессионных cookie и сетевого экрана (WAF)."""
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/128.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            async with self._session.get(f"{BASE_URL}/", headers=headers, timeout=15) as resp:
                await resp.read()
            self._session.cookie_jar.update_cookies(
                {"pgnig-ebok-device-token": self._device_id},
                URL(BASE_URL),
            )
        except Exception as err:
            _LOGGER.debug("Session pre-warm non-critical error: %s", err)

    async def login_api(self) -> str:
        """Прямой вход в систему через /auth/login."""
        await self._init_session()
        url = f"{BASE_URL}/auth/login?api-version=3.0"
        payload = {
            "identificator": self._username,
            "accessPin": self._password,
            "rememberLogin": False,
            "DeviceId": self._device_id,
            "DeviceName": "Home Assistant",
            "DeviceType": "Web",
        }
        try:
            async with self._session.post(
                url, json=payload, headers=self._headers(), timeout=30
            ) as resp:
                if resp.status == 401:
                    raise OrlenEbokAuthError("Неверный логин или пароль PGNiG eBOK")

                if resp.status != 200:
                    text = await resp.text()
                    try:
                        err_json = await resp.json()
                        err_msg = (
                            err_json.get("Message")
                            or str(err_json.get("Errors", ""))
                            or text[:200]
                        )
                    except Exception:
                        err_msg = text[:200]

                    if resp.status == 400 and any(
                        keyword in text.lower()
                        for keyword in ("błędn", "hasł", "login", "password", "nieprawidłow")
                    ):
                        raise OrlenEbokAuthError(f"Неверный логин или пароль PGNiG eBOK: {err_msg}")

                    raise OrlenEbokApiError(
                        f"Ошибка входа eBOK (HTTP {resp.status}): {err_msg}"
                    )

                data = await resp.json()
                token = data.get("Token")
                if not token:
                    raise OrlenEbokAuthError(
                        f"Ответ сервера не содержит 'Token': {data}"
                    )
                self._cached_token = token
                _LOGGER.debug("Авторизация Orlen eBOK успешно выполнена")
                return token
        except OrlenEbokAuthError:
            raise
        except OrlenEbokApiError:
            raise
        except asyncio.TimeoutError as err:
            raise OrlenEbokApiError("Таймаут соединения при авторизации") from err
        except aiohttp.ClientError as err:
            raise OrlenEbokApiError(f"Сетевая ошибка при авторизации: {err}") from err

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
                        _LOGGER.debug("Токен устарел (401), обновление сессии...")
                        self._cached_token = None
                        continue
                    if resp.status != 200:
                        text = await resp.text()
                        raise OrlenEbokApiError(
                            f"Вызов API {path} вернул HTTP {resp.status}: {text[:200]}"
                        )
                    return await resp.json()
            except asyncio.TimeoutError as err:
                raise OrlenEbokApiError(f"Таймаут при вызове {path}") from err
            except aiohttp.ClientError as err:
                raise OrlenEbokApiError(f"Сетевая ошибка при вызове {path}: {err}") from err

        raise OrlenEbokAuthError("Сессия истекла, повторный вход не удался")

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
            raise OrlenEbokApiError(f"Ошибка валидации показания (Код {code}): {msg}")
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
            raise OrlenEbokApiError(f"Сбой отправки показания (Код {code}): {msg}")
        return data

    async def cancel_reading(self, meter_number: str) -> dict[str, Any]:
        """Отмена ранее поданного показания."""
        payload = {"NumberOSD": str(meter_number)}
        data = await self._request("POST", "/crm/cancel-reading", json_data=payload)
        code = data.get("Code", -1)
        if code != 0:
            msg = data.get("Message") or f"Cancellation rejected with code {code}"
            raise OrlenEbokApiError(f"Сбой отмены показания (Код {code}): {msg}")
        return data

    async def get_all_agreements(self) -> list[dict[str, Any]]:
        """Получить список договоров и финансовый баланс (Saldo, Iban)."""
        data = await self._request("GET", "/crm/get-all-agreements")
        return data.get("Agreements", [])

    async def get_billing_summary(self) -> dict[str, Any]:
        """Получить статус расчетов (Billing summary)."""
        return await self._request("GET", "/crm/get-billing-summary")
