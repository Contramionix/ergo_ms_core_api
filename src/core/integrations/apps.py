"""
AppConfig для ``src.core.integrations``.

На этапе ``ready()``:

1. Конфигурирует ModuleBridge по ``BRIDGE_TRANSPORT`` / ``BRIDGE_EVENT_BUS``
   (``local`` | ``http`` / ``redis``).
2. Устанавливает runtime-страж изоляции модулей
   (см. :mod:`src.core.integrations.isolation`) согласно настройке
   ``BRIDGE_ISOLATION`` (``'off' | 'warn' | 'raise'``).
3. Регистрирует Django system check схем platform-контрактов
   (``BRIDGE_CONTRACTS``) — проверка выполняется после всех ``ready()``.

При ``local`` ``ModuleBridge.configure`` не вызывается — это важно, чтобы
не сбросить уже зарегистрированные провайдеры (порядок ``ready()``
Django-приложений строго не гарантируется). Для ``http``/``redis``
транспорт создаётся один раз до типичной регистрации integrations модулей
(integrations стоит рано в INSTALLED_APPS).
"""

from __future__ import annotations

import logging
from pathlib import Path

from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .contract_validation import VALID_MODES as CONTRACT_MODES, register_bridge_contract_checks
from .isolation import VALID_MODES, find_modules_dir, install_isolation_audit_hook

logger = logging.getLogger('integrations.bridge')

_VALID_TRANSPORTS = frozenset({'local', 'http'})
_VALID_EVENT_BUSES = frozenset({'local', 'redis'})


class IntegrationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'src.core.integrations'
    label = 'core_integrations'
    verbose_name = 'Module Bridge'

    def ready(self) -> None:
        from src.core.utils.django_cli import is_lean_schema_cli

        # migrate/makemigrations: Local* bridge по умолчанию достаточен, audit hook не нужен
        if is_lean_schema_cli():
            return

        from . import tasks as _core_outbox_tasks  # noqa: F401

        self._configure_bridge()
        self._install_isolation_guard()
        self._ensure_contract_mode()
        register_bridge_contract_checks()

    @staticmethod
    def _configure_bridge() -> None:
        from .bridge import ModuleBridge
        from .service_map import clear_service_map_cache
        from .transports import HttpTransport, RedisEventBus

        transport_name = (getattr(settings, 'BRIDGE_TRANSPORT', 'local') or 'local').strip().lower()
        event_bus_name = (getattr(settings, 'BRIDGE_EVENT_BUS', 'local') or 'local').strip().lower()

        if transport_name not in _VALID_TRANSPORTS:
            raise ImproperlyConfigured(
                f"BRIDGE_TRANSPORT={transport_name!r} не поддерживается. "
                f"Допустимо: {sorted(_VALID_TRANSPORTS)}."
            )
        if event_bus_name not in _VALID_EVENT_BUSES:
            raise ImproperlyConfigured(
                f"BRIDGE_EVENT_BUS={event_bus_name!r} не поддерживается. "
                f"Допустимо: {sorted(_VALID_EVENT_BUSES)}."
            )

        clear_service_map_cache()

        if transport_name == 'local' and event_bus_name == 'local':
            logger.debug("ModuleBridge stays on default Local* implementations")
            return

        transport = None
        event_bus = None
        if transport_name == 'http':
            transport = HttpTransport()
        if event_bus_name == 'redis':
            event_bus = RedisEventBus()

        # Не трогаем сторону, оставшуюся local — иначе сбросим уже
        # зарегистрированные провайдеры/подписки из более ранних ready().
        kwargs = {}
        if transport is not None:
            kwargs['transport'] = transport
        if event_bus is not None:
            kwargs['event_bus'] = event_bus
        if kwargs:
            ModuleBridge.configure(**kwargs)
            logger.info(
                "ModuleBridge configured: transport=%s event_bus=%s",
                transport_name,
                event_bus_name,
            )

    @staticmethod
    def _install_isolation_guard() -> None:
        mode = (getattr(settings, 'BRIDGE_ISOLATION', 'warn') or 'warn').strip().lower()
        if mode not in VALID_MODES:
            raise ImproperlyConfigured(
                f"Unknown BRIDGE_ISOLATION value: {mode!r}. "
                f"Expected one of {VALID_MODES}."
            )

        modules_dir = getattr(settings, 'MODULES_DIR', None)
        if modules_dir is None:
            base_dir = getattr(settings, 'BASE_DIR', None)
            if base_dir is not None:
                modules_dir = find_modules_dir(Path(base_dir))

        install_isolation_audit_hook(mode=mode, modules_dir=modules_dir)

    @staticmethod
    def _ensure_contract_mode() -> None:
        mode = (getattr(settings, 'BRIDGE_CONTRACTS', 'warn') or 'warn').strip().lower()
        if mode not in CONTRACT_MODES:
            raise ImproperlyConfigured(
                f"Unknown BRIDGE_CONTRACTS value: {mode!r}. "
                f"Expected one of {sorted(CONTRACT_MODES)}."
            )
