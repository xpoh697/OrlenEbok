# ORLEN eBOK (dawniej PGNiG) — Home Assistant Integration

Интеграция Home Assistant (HACS) для личного кабинета **ORLEN mBOK / PGNiG eBOK** (`com.pgnig_bok`), созданная на основе реверс-инжиниринга официального мобильного приложения и веб-портала CRM API v3.0 (`https://ebok.myorlen.pl`).

Интеграция позволяет:
- Авторизоваться по логину и паролю личного кабинета.
- Отслеживать последнее зафиксированное показание счетчика газа и дату фиксации.
- Контролировать статус расчетного окна для подачи показаний.
- **Подавать показания счетчика газа в автоматизациях Home Assistant** через службу `orlen_ebok.submit_reading` (включая автоматическое считывание из локального сенсора газа, например ESPHome / Zigbee).

---

## Создаваемые сенсоры

Для каждого газового счетчика (PPG) создаются 3 сенсора:

1. `sensor.<ppg>_last_reading`:
   - **Имя**: Последнее показание
   - **Класс устройства**: `gas`
   - **Класс состояния**: `total_increasing`
   - **Единица измерения**: $m^3$
   - **Атрибуты**: `meter_number`, `tariff`, `reading_type`.

2. `sensor.<ppg>_last_reading_date`:
   - **Имя**: Дата последнего показания
   - **Класс устройства**: `timestamp`
   - **Значение**: ISO-дата последней фиксации показания в биллинге.

3. `sensor.<ppg>_reading_submission_status`:
   - **Имя**: Статус передачи показаний
   - **Состояния**:
     - `allowed` — расчетное окно открыто, передача разрешена;
     - `already_submitted` — показание за текущий расчетный период уже успешно подано;
     - `disallowed` — окно подачи показаний закрыто;
     - `submitted` — показание было успешно передано в текущей сессии.
   - **Атрибуты**: `allowed_dates`, `meter_number`, `last_submission_date`, `last_submission_result`.

---

## Службы (Services)

### `orlen_ebok.submit_reading`
Передает показания счетчика газа в CRM биллинг ORLEN.

**Параметры:**
- `value` *(опционально, если настроен `source_sensor`)*: Числовое значение показания в $m^3$ (целое число или дробное, которое автоматически округляется вниз).
- `ppg_id` *(опционально)*: Номер точки учета PPG. Обязателен только если на аккаунте зарегистрировано несколько счетчиков.
- `date` *(опционально)*: Дата показания в формате `YYYY-MM-DD` (по умолчанию текущая дата).
- `consent_meter_reset` *(опционально, по умолчанию `false`)*: Флаг подтверждения, если счетчик был заменен и новое показание меньше предыдущего.

Перед отправкой служба автоматически:
1. Очищает входные данные и приводит их к `int` (`math.floor`).
2. Проверяет монотонное возрастание (`val >= previous_val`).
3. Вызывает предварительный валидатор `/crm/validate-ppg-reading`.
4. Фиксирует показание через `/crm/add-ppg-reading-v2`.
5. Оптимистично обновляет статус в `submitted` и генерирует событие `orlen_ebok_reading_submitted`.

### `orlen_ebok.cancel_reading`
Отменяет ошибочно поданное показание через эндпоинт `/crm/cancel-reading`.

---

## Пример автоматизации Home Assistant

```yaml
alias: "Ежемесячная подача показаний счетчика газа в ORLEN eBOK"
description: "Отправляет показания газа 1-го числа каждого месяца, если окно подачи открыто"
trigger:
  - platform: time
    at: "10:00:00"
condition:
  - condition: template
    value_template: "{{ now().day == 1 }}"
  - condition: state
    entity_id: sensor.licznik_gazu_reading_submission_status
    state: "allowed"
action:
  - service: orlen_ebok.submit_reading
    data:
      value: "{{ states('sensor.gas_meter_pulses') }}"
```

---

## Установка

### Через HACS (Custom Repository)
1. Откройте **HACS** -> **Интеграции** -> три точки в правом верхнем углу -> **Пользовательские репозитории**.
2. Вставьте URL: `https://github.com/xpoh697/OrlenEbok`.
3. Категория: `Integration`.
4. Нажмите **Добавить**, найдите **ORLEN eBOK (PGNiG)** и нажмите **Установить**.
5. Перезагрузите Home Assistant.
6. В разделе **Настройки** -> **Устройства и службы** нажмите **Добавить интеграцию** и найдите **ORLEN eBOK (PGNiG)**.
7. Введите логин и пароль от личного кабинета.

### Вручную
Скопируйте папку `custom_components/orlen_ebok` в директорию `config/custom_components/` вашего Home Assistant и перезагрузите сервер.

---

## Лицензия
MIT License.
