"""Срок хранения сессий устройств (UserDevice)."""

from src.config.security_profile_runtime import session_device_retention_days

# 0 — purge выключен. Beat + ergoms api session_device_purge.
API_SESSION_DEVICE_RETENTION_DAYS = session_device_retention_days()
