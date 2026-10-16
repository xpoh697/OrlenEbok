# Техническое задание (ТЗ): Интеграция Orlen / PGNiG eBOK для Home Assistant

## 1. Введение и цель проекта
Разработка пользовательской интеграции Home Assistant (HACS) для личного кабинета клиентов ORLEN mBOK / PGNiG eBOK (`com.pgnig_bok`), позволяющей:
1. Авторизоваться по прямому логину и паролю (PIN) пользователя классического PGNiG eBOK через UI Home Assistant (Config Flow).
2. Предоставить сервис `orlen_ebok.submit_reading` для передачи показаний счетчика газа, вызываемый из регулярных автоматизаций Home Assistant (например, 1-го числа каждого месяца).
3. Создать 3 ключевых сенсора для каждого счетчика:
   - **Последнее показание** (`sensor.<ppg>_last_reading`): зафиксированное значение в кубических метрах ($m^3$).
   - **Дата последнего показания** (`sensor.<ppg>_last_reading_date`): дата и время последней фиксации показания.
   - **Статус передачи показания** (`sensor.<ppg>_reading_submission_status`): текущее состояние возможности отправки (`allowed` — окно открыто, `already_submitted` — показание за период уже подано, `disallowed` — окно закрыто, `submitted` — успешно отправлено).
4. Обеспечить корректное брендирование и интеграцию в каталог HACS с официальной иконкой и логотипом приложения (по стандарту EMS).

## 2. Результаты реверс-инжиниринга и спецификация прямого API Login
Прямой вход классического личного кабинета eBOK PGNiG / myORLEN использует endpoint `auth/login`:

### 2.1. Спецификация прямого входа
- **URL**: `POST https://ebok.myorlen.pl/auth/login?api-version=3.0`
- **Обязательная инициализация сессии**:
  - Предварительный вызов `GET https://ebok.myorlen.pl/` для получения сессионных кук WAF.
  - Установка cookie `pgnig-ebok-device-token: <device_id>` в сессию.
- **Обязательные заголовки**:
  - `Origin: https://ebok.myorlen.pl`
  - `Referer: https://ebok.myorlen.pl/`
  - `Accept: application/json`
  - `Content-Type: application/json`
  - `User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36`
- **Тело запроса (JSON)**:
  ```json
  {
    "identificator": "<логин_пользователя>",
    "accessPin": "<пароль_пользователя>",
    "rememberLogin": false,
    "DeviceId": "<32_hex_device_id>",
    "DeviceName": "Home Assistant",
    "DeviceType": "Web"
  }
  ```
  *(КРИТИЧЕСКИ ВАЖНО: Параметр `DeviceType` строго должен быть `"Web"`. При значении `"Android"` API шлюз возвращает HTTP 400 Bad Request: "The field DeviceType is invalid.").*

### 2.2. Ответ сервера на авторизацию
- **Успех (HTTP 200)**:
  Возвращает JSON с полем `Token: "..."` (JWT токен сессии). Токен передается в последующих запросах в заголовке `AuthToken: <token>`.
- **Ошибка учетных данных (HTTP 401)**:
  Неверный логин или пароль.
- **Иные ошибки (HTTP 400/403/500)**:
  В теле возвращается детальное описание ошибки (`Errors`, `Message`), которое должно отображаться в логах Home Assistant.

### 2.3. Спецификация эндпоинтов CRM API v3.0
1. **Список точек учета и счетчиков**:
   - `GET /crm/get-ppg-list?api-version=3.0`
   - Возвращает массив `PpgList`: `IdPPG`, `MeterNumber`, `ContractNumber`, `Tariff`, `AddReadingMode` (`AllowBeforePeriods` / `Disallow`), `ReadingAdded`, `ReadingDateUtc`.
2. **История показаний счетчика**:
   - `GET /crm/get-all-ppg-readings-for-meter?pageSize=10&pageNumber=1&api-version=3.0&idPpg={idPpg}`
   - Возвращает массив `MeterReadings`: `ReadingDateUtc`, `Value`, `Wear`, `Type`.
3. **Даты и периоды подачи показаний**:
   - `GET /crm/get-ppg-readings-dates?idPpg={idPpg}&api-version=3.0`
   - Возвращает допустимые даты расчётных окон `MeterReadingsDates`.
4. **Валидация показания перед отправкой**:
   - `POST /crm/validate-ppg-reading?api-version=3.0`
   - Тело: `{"IdPP": "{IdPPG}", "Value": {reading_int}, "ConsentMeterReset": false}`
   - Коды ответа: `0` (валидно), `1` / `1031` (ошибка показания), `1040` (требуется подтверждение сброса счетчика).
5. **Подача показания счетчика**:
   - `POST /crm/add-ppg-reading-v2?api-version=3.0`
   - Тело:
     ```json
     {
       "DateReading": "YYYY-MM-DD",
       "OsdNumber": "{IdPPG}",
       "Value": {reading_int},
       "ConsentMeterReset": false,
       "UseDate": true,
       "SourceFromWWW": true
     }
     ```
   - Успешный ответ: `Code == 0`, объект `Reading` с подтвержденным значением и `CanBeCancelled: true`.
6. **Отмена недавно поданного показания**:
   - `POST /crm/cancel-reading?api-version=3.0`
   - Тело: `{"NumberOSD": "{MeterNumber}"}`

## 3. Архитектура интеграции для Home Assistant

### 3.1. Структура файлов компонента
```
custom_components/orlen_ebok/
├── brand/
│   ├── icon.png           # Фирменная иконка бренда
│   └── logo.png           # Фирменный логотип бренда
├── __init__.py            # Инициализация платформы, регистрация служб
├── manifest.json          # Метаданные компонента HACS
├── const.py               # Константы, эндпоинты, ключи конфигурации
├── config_flow.py         # UI мастер настройки (прямой логин/пароль PGNiG eBOK)
├── coordinator.py         # DataUpdateCoordinator (опрос API, кэширование)
├── api.py                 # Асинхронный клиент прямого API PGNiG eBOK на aiohttp
├── sensor.py              # Реализация 3 сенсоров (последнее показание, дата, статус)
├── services.yaml          # Описание параметров службы submit_reading для автоматизаций
├── strings.json           # Описания и локализация
└── translations/
    ├── en.json
    ├── pl.json
    └── ru.json
```

### 3.2. Требуемые сенсоры
1. `sensor.<ppg>_last_reading`: последнее зафиксированное показание (unit: `m³`, device_class: `gas`, state_class: `total_increasing`).
2. `sensor.<ppg>_last_reading_date`: дата и время последнего зафиксированного показания (device_class: `timestamp`).
3. `sensor.<ppg>_reading_submission_status`: статус передачи показаний (`allowed`, `already_submitted`, `disallowed`, `submitted`). В атрибутах: `meter_number`, `tariff`, `reading_dates`, `last_submission_result`.

### 3.3. Служба (Service) для автоматизаций
- `orlen_ebok.submit_reading`:
  - `value`: (обязательно или берется из `source_sensor`) целочисленное показание счетчика в $m^3$.
  - `ppg_id`: (опционально, если на аккаунте один счетчик — выбирается автоматически).
  - `date`: (опционально, формат `YYYY-MM-DD`, по умолчанию текущая дата).
  - `consent_meter_reset`: (опционально, `false` по умолчанию).

### 3.4. Требования к брендингу HACS (по образцу EMS)
- Иконка и логотип размещены в `custom_components/orlen_ebok/brand/`, а также в корне и `images/`.
- Конфигурация `hacs.json` включает `"country": "PL"`, `"homeassistant": "2024.1.0"`, `"hacs": "1.0.0"`.

## 4. Требования к надежности и безопасности
1. **Защита от отправки некорректных показаний**:
   - Автоматическое приведение значения через `math.floor()`.
   - Проверка: `value >= last_reading_value`.
   - Обязательный предварительный вызов `validate-ppg-reading` перед отправкой.
   - Защита от дублирующего вызова из автоматизаций, если показание уже подано (`ReadingAdded == true`).
2. **Асинхронность и неблокирующий I/O**:
   - Использование `aiohttp.ClientSession` через `async_get_clientsession(hass)`.
3. **Безопасность учетных данных**:
   - Хранение логина и пароля в зашифрованном хранилище `config_entries`.
