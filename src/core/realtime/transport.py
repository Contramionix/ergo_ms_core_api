"""Проверка режима realtime-транспорта из настроек Django."""

from django.conf import settings


def is_websocket_transport() -> bool:
    return getattr(settings, 'REALTIME_TRANSPORT', 'websocket') == 'websocket'
