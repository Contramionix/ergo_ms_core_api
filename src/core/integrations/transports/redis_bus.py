"""
Redis EventBus ModuleBridge (BRIDGE_EVENT_BUS=redis).

Локальные подписчики вызываются синхронно; дополнительно событие
публикуется в Redis для обработчиков на других процессах.
``emit`` возвращает результаты только локальных обработчиков.

В Redis уходит JSON: объект ``user`` заменяется на ``user_id`` и
``user_public_id``. ORM, request и прочие несериализуемые значения
не публикуются (как в HTTP-мосте).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime
from typing import Any, Callable
from uuid import UUID

from django.conf import settings

logger = logging.getLogger('integrations.bridge.redis')

_CHANNEL_PREFIX = 'ergo:bridge:events:'
_listener_lock = threading.Lock()
_SKIP_PAYLOAD_KEYS = frozenset({'user', 'user_id', 'user_public_id'})


def _is_user_like(value: Any) -> bool:
    if value is None or isinstance(value, (str, bytes, int, float, bool, dict, list, tuple)):
        return False
    if getattr(value, 'pk', None) is None and getattr(value, 'public_id', None) is None:
        return False
    return hasattr(value, 'is_authenticated')


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    raise TypeError(f'Bridge Redis: значение типа {type(value).__name__} нельзя сериализовать в JSON')


def _event_payload_for_redis(payload: dict[str, Any]) -> dict[str, Any]:
    """Копия payload для pub/sub: user → идентификаторы, остальное — JSON-примитивы."""
    safe: dict[str, Any] = {}
    user = payload.get('user')
    user_id = payload.get('user_id')
    user_public_id = payload.get('user_public_id')
    if _is_user_like(user):
        if user_id is None:
            user_id = getattr(user, 'pk', None)
        if not user_public_id:
            public_id = getattr(user, 'public_id', None)
            user_public_id = str(public_id) if public_id else None
        user = None
    elif user is not None:
        try:
            user = _jsonable(user)
        except TypeError:
            user = None

    if user is not None:
        safe['user'] = user
    if user_id is not None:
        try:
            safe['user_id'] = int(user_id)
        except (TypeError, ValueError):
            pass
    if user_public_id:
        safe['user_public_id'] = str(user_public_id)

    for key, value in payload.items():
        if key in _SKIP_PAYLOAD_KEYS:
            continue
        try:
            safe[str(key)] = _jsonable(value)
        except TypeError:
            logger.debug(
                'Bridge Redis: пропуск payload %s (%s)',
                key,
                type(value).__name__,
            )
    return safe


def _redis_client():
    from src.config.redis_runtime import redis_host, redis_port, redis_url

    try:
        import redis
    except ImportError:
        logger.error('Пакет redis не установлен — RedisEventBus недоступен')
        return None

    db = getattr(settings, 'BRIDGE_REDIS_DB', None)
    if db is None:
        db = 4
    try:
        db = int(db)
    except (TypeError, ValueError):
        db = 4

    url = redis_url(db)
    try:
        return redis.Redis.from_url(url, decode_responses=True)
    except Exception:
        # fallback host/port
        try:
            return redis.Redis(host=redis_host(), port=redis_port(), db=db, decode_responses=True)
        except Exception:
            logger.exception('Не удалось подключить Redis для bridge EventBus')
            return None


class RedisEventBus:
    """Local handlers + Redis pub/sub fan-out."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[..., Any]]] = {}
        self._lock = threading.RLock()
        self._listener_started = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pubsub = None

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
        self._ensure_listener()

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

        self._publish(event, payload)
        return results

    def reset(self) -> None:
        with self._lock:
            self._subscribers.clear()
        self._stop_listener()

    def _publish(self, event: str, payload: dict[str, Any]) -> None:
        client = _redis_client()
        if client is None:
            return
        try:
            body = json.dumps(
                {
                    'event': event,
                    'payload': _event_payload_for_redis(payload),
                    'origin': id(self),
                }
            )
            client.publish(f'{_CHANNEL_PREFIX}{event}', body)
            # также общий канал для подписки «на всё»
            client.publish(f'{_CHANNEL_PREFIX}*', body)
        except Exception:
            logger.exception("Не удалось опубликовать bridge event '%s'", event)

    def _ensure_listener(self) -> None:
        with _listener_lock:
            if self._listener_started:
                return
            client = _redis_client()
            if client is None:
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._listen_loop,
                name='bridge-redis-eventbus',
                daemon=True,
            )
            self._listener_started = True
            self._thread.start()

    def _stop_listener(self) -> None:
        self._stop.set()
        try:
            if self._pubsub is not None:
                self._pubsub.close()
        except Exception:
            pass
        self._pubsub = None
        self._listener_started = False

    def _listen_loop(self) -> None:
        client = _redis_client()
        if client is None:
            self._listener_started = False
            return
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        self._pubsub = pubsub
        try:
            pubsub.psubscribe(f'{_CHANNEL_PREFIX}*')
            for message in pubsub.listen():
                if self._stop.is_set():
                    break
                if message is None or message.get('type') not in ('pmessage', 'message'):
                    continue
                raw = message.get('data')
                if not isinstance(raw, str):
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                event = data.get('event')
                payload = data.get('payload') or {}
                origin = data.get('origin')
                if origin == id(self):
                    # своё же сообщение — локальные handlers уже отработали в emit
                    continue
                if not event or not isinstance(payload, dict):
                    continue
                with self._lock:
                    handlers = list(self._subscribers.get(event, []))
                for handler in handlers:
                    try:
                        handler(**payload)
                    except Exception:
                        logger.exception(
                            "Remote event handler %s for '%s' raised",
                            handler,
                            event,
                        )
        except Exception:
            logger.exception('Bridge Redis listener stopped with error')
        finally:
            try:
                pubsub.close()
            except Exception:
                pass
            self._listener_started = False
