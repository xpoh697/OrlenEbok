"""Константы для интеграции ORLEN eBOK."""

DOMAIN = "orlen_ebok"
BASE_URL = "https://ebok.myorlen.pl"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_SOURCE_SENSOR = "source_sensor"
CONF_PPG_ID = "ppg_id"

ATTR_PPG_ID = "ppg_id"
ATTR_METER_NUMBER = "meter_number"
ATTR_TARIFF = "tariff"
ATTR_VALUE = "value"
ATTR_DATE = "date"
ATTR_CONSENT_METER_RESET = "consent_meter_reset"
ATTR_ALLOWED_DATES = "allowed_dates"
ATTR_READING_TYPE = "reading_type"
ATTR_LAST_SUBMISSION_RESULT = "last_submission_result"
ATTR_LAST_SUBMISSION_DATE = "last_submission_date"

DEFAULT_UPDATE_INTERVAL_HOURS = 6

SERVICE_SUBMIT_READING = "submit_reading"
SERVICE_CANCEL_READING = "cancel_reading"

EVENT_READING_SUBMITTED = "orlen_ebok_reading_submitted"
EVENT_READING_CANCELLED = "orlen_ebok_reading_cancelled"

STATUS_ALLOWED = "allowed"
STATUS_ALREADY_SUBMITTED = "already_submitted"
STATUS_DISALLOWED = "disallowed"
STATUS_SUBMITTED = "submitted"
