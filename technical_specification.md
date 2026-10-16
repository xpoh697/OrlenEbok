# Техническое задание (ТЗ): Интеграция Orlen / PGNiG eBOK для Home Assistant

## 1. Введение и цель проекта
Разработка пользовательской интеграции Home Assistant (HACS) для личного кабинета клиентов ORLEN mBOK / PGNiG eBOK (`com.pgnig_bok`), позволяющей:
1. Авторизоваться по логину и паролю пользователя через UI Home Assistant (Config Flow).
2. Предоставить сервис `orlen_ebok.submit_reading` для передачи показаний счетчика газа, вызываемый из регулярных автоматизаций Home Assistant (например, 1-го числа каждого месяца).
3. Создать 3 ключевых сенсора для каждого счетчика:
   - **Последнее показание** (`sensor.<ppg>_last_reading`): зафиксированное значение в кубических метрах ($m^3$).
   - **Дата последнего показания** (`sensor.<ppg>_last_reading_date`): дата и время последней фиксации показания.
   - **Статус передачи показания** (`sensor.<ppg>_reading_submission_status`): текущее состояние возможности отправки (`allowed` — окно открыто, `already_submitted` — показание за период уже подано, `disallowed` — окно закрыто, `submitted` — успешно отправлено).
4. Обеспечить корректное брендирование и интеграцию в каталог HACS с официальной иконкой и логотипом приложения (по стандарту EMS).

## 2. Результаты реверс-инжиниринга мобильного приложения и веб-портала
Приложение `com.pgnig_bok` (Flutter + native AOT / Android XAPK) и веб-портал `https://ebok.myorlen.pl` используют единый REST API версии 3.0:

### 2.1. Базовые URL и аутентификация
- **Host**: `https://ebok.myorlen.pl`
- **Аутентификация**:
  - `POST /auth/login?api-version=3.0` (прямой API login с `identificator`, `accessPin`, `DeviceId`, `DeviceType: "Android"`)
- **Заголовки запросов**:
  - `AuthToken: <token>`
  - `Content-Type: application/json`
  - `Accept: application/json`

### 2.2. Спецификация ключевых API-эндпоинтов
1. **Список точек учета и счетчиков**:
   - `GET /crm/get-ppg-list?api-version=3.0`
   - Возвращает массив `PpgList`: `IdPPG`, `MeterNumber`, `ContractNumber`, `Tariff`, `AddReadingMode` (`AllowBeforePeriods` / `Disallow`), `ReadingAdded`, `ReadingDateUtc`, `Address`.
2. **История показаний счетчика**:
   - `GET /crm/get-all-ppg-readings-for-meter?pageSize=10&pageNumber=1&api-version=3.0&idPpg={idPpg}`
   - Возвращает массив `MeterReadings`: `ReadingDateUtc`, `Value`, `Wear`, `Type`.
3. **Даты и периоды подачи показаний**:
   - `GET /crm/get-ppg-readings-dates?idPpg={idPpg}&api-version=3.0`
   - Возвращает допустимые даты расчётных окон `MeterReadingsDates`.
4. **Валидация показания перед отправкой**:
   - `POST /crm/validate-ppg-reading?api-version=3.0`
   - Тело: `{"IdPP": "{IdPPG}", "Value": {reading_int}, "ConsentMeterReset": false}`
   - Коды ответа: `0` (валидно), `1` / `1031` (ошибка: меньше предыдущего или недопустимый прирост), `1040` (требуется подтверждение сброса счетчика).
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
│   ├── icon.png           # Фирменная иконка бренда (480x480 / 512x512)
│   └── logo.png           # Фирменный логотип бренда
├── __init__.py            # Инициализация платформы, регистрация служб
├── manifest.json          # Метаданные компонента HACS
├── const.py               # Константы, эндпоинты, ключи конфигурации
├── config_flow.py         # UI мастер настройки (логин, пароль, опциональный source_sensor)
├── coordinator.py         # DataUpdateCoordinator (опрос API, кэширование)
├── api.py                 # Асинхронный клиент Orlen/PGNiG API на aiohttp
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
1. Размещение фирменных ассетов:
   - `custom_components/orlen_ebok/brand/icon.png`
   - `custom_components/orlen_ebok/brand/logo.png`
   - Дополнительное размещение в корне `icon.png` и `images/icon.png` для корректного парсинга различными версиями HACS и GitHub README.
2. Конфигурация `hacs.json`:
   ```json
   {
     "name": "ORLEN eBOK (PGNiG)",
     "render_readme": true,
     "hacs": "1.0.0",
     "hide_default_branch": true,
     "homeassistant": "2024.1.0",
     "country": "PL"
   }
   ```

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
