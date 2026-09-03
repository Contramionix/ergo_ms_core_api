"""
ModuleBridge — фасад единого механизма межмодульного взаимодействия.

Публичный API (стабильный):

    bridge.provide(name, handler, *, override=False)
    bridge.provide_many(group, key, obj)
    bridge.subscribe(event, handler)

    bridge.has(name) -> bool
    bridge.call(name, *args, default=None, **kwargs) -> Any
    bridge.emit(event, **payload) -> list[Any]
    bridge.all(group) -> dict[str, Any]

    bridge.unregister(name)
    bridge.unsubscribe(event, handler)
    bridge.reset()                                  # для тестов

Декораторы:

    @bridge.provide_op('module.operation')
    def op(...): ...

    @bridge.subscribe_to('module.event')
    def handler(**payload): ...

Все методы делегируются Transport (для операций) и EventBus (для событий).
По умолчанию LocalTransport / LocalEventBus (монолит).
``BRIDGE_TRANSPORT=http`` и ``BRIDGE_EVENT_BUS=redis`` — режим microservice.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .transports import EventBus, LocalEventBus, LocalTransport, Transport

logger = logging.getLogger('integrations.bridge')


def _migrate_transport_registry(old: Any, new: Any) -> None:
    old_providers = getattr(old, '_providers', None)
    new_providers = getattr(new, '_providers', None)
    if isinstance(old_providers, dict) and isinstance(new_providers, dict):
        for name, handler in old_providers.items():
            new_providers.setdefault(name, handler)
    old_groups = getattr(old, '_groups', None)
    new_groups = getattr(new, '_groups', None)
    if isinstance(old_groups, dict) and isinstance(new_groups, dict):
        for group, providers in old_groups.items():
            target = new_groups.setdefault(group, {})
            for key, obj in providers.items():
                target.setdefault(key, obj)


def _migrate_event_bus_registry(old: Any, new: Any) -> None:
    old_subs = getattr(old, '_subscribers', None)
    new_subs = getattr(new, '_subscribers', None)
    if not isinstance(old_subs, dict) or not isinstance(new_subs, dict):
        return
    for event, handlers in old_subs.items():
        bucket = new_subs.setdefault(event, [])
        for handler in handlers:
            if handler not in bucket:
                bucket.append(handler)


class ModuleBridge:
    """Фасад над Transport + EventBus. Используется как класс-синглтон."""

    _transport: Transport = LocalTransport()
    _event_bus: EventBus = LocalEventBus()

    @classmethod
    def configure(cls, *, transport: Transport | None = None,
                  event_bus: EventBus | None = None) -> None:
        """
        Подменить транспорт/шину (например, при старте Django через settings
        или в тестах). Провайдеры/подписки с предыдущей in-memory реализации
        переносятся на новую, если обе стороны поддерживают реестр.
        """
        if transport is not None:
            old_transport = cls._transport
            cls._transport = transport
            _migrate_transport_registry(old_transport, transport)
            logger.info("ModuleBridge transport set to %s", type(transport).__name__)
        if event_bus is not None:
            old_bus = cls._event_bus
            cls._event_bus = event_bus
            _migrate_event_bus_registry(old_bus, event_bus)
            logger.info("ModuleBridge event bus set to %s", type(event_bus).__name__)

    # --- single-provider operations ---------------------------------------

    @classmethod
    def provide(
        cls,
        name: str,
        handler: Callable[..., Any],
        *,
        override: bool = False,
    ) -> None:
        cls._transport.provide(name, handler, override=override)

    @classmethod
    def provide_op(cls, name: str, *, override: bool = False) -> Callable:
        """Декораторная форма provide(). Применять к функции-обработчику."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            cls._transport.provide(name, func, override=override)
            return func

        return decorator

    @classmethod
    def has(cls, name: str) -> bool:
        return cls._transport.has(name)

    @classmethod
    def call(
        cls,
        name: str,
        *args: Any,
        default: Any = None,
        **kwargs: Any,
    ) -> Any:
        """
        Вызвать зарегистрированную операцию по строковому имени.

        Если провайдер отсутствует — вернуть default (по умолчанию None).
        Если транспорт до peer сломался — BridgeUnavailable, не default.
        """
        return cls._transport.call(name, args, kwargs, default)

    @classmethod
    def unregister(cls, name: str) -> None:
        cls._transport.unregister(name)

    # --- multi-provider groups -------------------------------------------

    @classmethod
    def provide_many(cls, group: str, key: str, obj: Any) -> None:
        cls._transport.provide_many(group, key, obj)

    @classmethod
    def all(cls, group: str) -> dict[str, Any]:
        return cls._transport.all(group)

    @classmethod
    def unregister_many(cls, group: str, key: str) -> None:
        cls._transport.unregister_many(group, key)

    @classmethod
    def local_providers(cls) -> dict[str, Any]:
        """
        Read-only снимок локально зарегистрированных single-op handlers.

        Для internal RPC и валидации контрактов — без обхода remote HTTP.
        """
        getter = getattr(cls._transport, 'local_providers', None)
        if callable(getter):
            return getter()
        return {}

    @classmethod
    def local_group(cls, group: str) -> dict[str, Any]:
        """Read-only снимок локальных провайдеров группы (без remote merge)."""
        getter = getattr(cls._transport, 'local_group', None)
        if callable(getter):
            return getter(group)
        return {}

    # --- events ----------------------------------------------------------

    @classmethod
    def subscribe(cls, event: str, handler: Callable[..., Any]) -> None:
        cls._event_bus.subscribe(event, handler)

    @classmethod
    def subscribe_to(cls, event: str) -> Callable:
        """Декораторная форма subscribe(). Применять к функции-обработчику."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            cls._event_bus.subscribe(event, func)
            return func

        return decorator

    @classmethod
    def unsubscribe(cls, event: str, handler: Callable[..., Any]) -> None:
        cls._event_bus.unsubscribe(event, handler)

    @classmethod
    def emit(cls, event: str, **payload: Any) -> list[Any]:
        """
        Вещать событие с именем event. Возвращает список результатов
        всех подписчиков. Если подписчиков нет — пустой список.
        """
        return cls._event_bus.emit(event, payload)

    @classmethod
    def emit_first(cls, event: str, **payload: Any) -> Any:
        """
        Вещать событие и вернуть первый не-None результат подписчиков
        (удобно для permission-check и подобных голосований).
        """
        for result in cls._event_bus.emit(event, payload):
            if result is not None:
                return result
        return None

    # --- testing ---------------------------------------------------------

    @classmethod
    def reset(cls) -> None:
        cls._transport.reset()
        cls._event_bus.reset()


bridge = ModuleBridge
