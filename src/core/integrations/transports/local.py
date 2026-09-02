"""
In-process реализации Transport и EventBus.

Используется при BRIDGE_TRANSPORT='local' / BRIDGE_EVENT_BUS='local'.
Хранит операции и подписчиков
в потокобезопасных словарях, вызывает их напрямую в текущем процессе.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from ..exceptions import DuplicateProvider
from .bind_kwargs import kwargs_accepted_by_handler

logger = logging.getLogger('integrations.bridge.local')


class LocalTransport:
    """Хранит провайдеров в памяти, вызывает их напрямую."""

    def __init__(self) -> None:
        self._providers: dict[str, Callable[..., Any]] = {}
        self._groups: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def provide(
        self,
        name: str,
        handler: Callable[..., Any],
        *,
        override: bool = False,
    ) -> None:
        if not name:
            raise ValueError("Operation name is required")
        if not callable(handler):
            raise TypeError(f"Handler for '{name}' must be callable")
        with self._lock:
            existing = self._providers.get(name)
            if existing is not None and existing is not handler and not override:
                raise DuplicateProvider(
                    f"Provider for '{name}' already registered: {existing!r}"
                )
            self._providers[name] = handler
            logger.debug("Registered provider '%s'", name)

    def provide_many(self, group: str, key: str, obj: Any) -> None:
        if not group:
            raise ValueError("Group name is required")
        if not key:
            raise ValueError("Provider key is required")
        with self._lock:
            providers = self._groups.setdefault(group, {})
            providers[key] = obj
            logger.debug("Registered '%s' provider for group '%s'", key, group)

    def has(self, name: str) -> bool:
        with self._lock:
            return name in self._providers

    def call(
        self,
        name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        default: Any,
    ) -> Any:
        with self._lock:
            handler = self._providers.get(name)
        if handler is None:
            return default
        return handler(*args, **kwargs_accepted_by_handler(handler, kwargs))

    def all(self, group: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._groups.get(group, {}))

    def unregister(self, name: str) -> None:
        with self._lock:
            self._providers.pop(name, None)

    def unregister_many(self, group: str, key: str) -> None:
        with self._lock:
            providers = self._groups.get(group)
            if providers and key in providers:
                del providers[key]
                if not providers:
                    del self._groups[group]

    def reset(self) -> None:
        with self._lock:
            self._providers.clear()
            self._groups.clear()

    def local_providers(self) -> dict[str, Callable[..., Any]]:
        """Копия локального реестра single-op (без remote)."""
        with self._lock:
            return dict(self._providers)

    def local_group(self, group: str) -> dict[str, Any]:
        """Копия локальных провайдеров группы (без remote merge)."""
        with self._lock:
            return dict(self._groups.get(group, {}))


class LocalEventBus:
    """Хранит подписчиков в памяти, вызывает их синхронно."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[..., Any]]] = {}
        self._lock = threading.RLock()

    def subscribe(self, event: str, handler: Callable[..., Any]) -> None:
        if not event:
            raise ValueError("Event name is required")
        if not callable(handler):
            raise TypeError(f"Handler for event '{event}' must be callable")
        with self._lock:
            handlers = self._subscribers.setdefault(event, [])
            if handler in handlers:
                return
            handlers.append(handler)
            logger.debug("Subscribed to event '%s'", event)

    def unsubscribe(self, event: str, handler: Callable[..., Any]) -> None:
        with self._lock:
            handlers = self._subscribers.get(event)
            if handlers and handler in handlers:
                handlers.remove(handler)
                if not handlers:
                    del self._subscribers[event]

    def emit(self, event: str, payload: dict[str, Any]) -> list[Any]:
        with self._lock:
            handlers = list(self._subscribers.get(event, []))

        results: list[Any] = []
        for handler in handlers:
            try:
                results.append(handler(**payload))
            except Exception:
                logger.exception(
                    "Event handler %s for '%s' raised", handler, event
                )
                results.append(None)
        return results

    def reset(self) -> None:
        with self._lock:
            self._subscribers.clear()
