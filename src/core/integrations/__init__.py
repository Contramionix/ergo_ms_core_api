"""
Module Bridge — единый механизм межмодульного взаимодействия.

Модули регистрируют операции и подписки по строковым именам
('module.operation', 'module.event'), потребители обращаются
через bridge.call / bridge.emit без прямых импортов другого модуля.

Пример провайдера (``modules/<name>/api/integrations.py``):

    from src.core.integrations import bridge
    from .models import MyEntity

    @bridge.provide_op('<name>.get_user_entity_ids')
    def _get_user_entity_ids(*, user=None, user_id=None, user_public_id=None, **_):
        resolved = user  # монолит: ORM; HTTP: собрать по user_public_id
        if not resolved or not getattr(resolved, 'is_authenticated', False):
            return []
        return list(
            MyEntity.objects
            .filter(user=resolved)
            .values_list('id', flat=True)
        )

    @bridge.subscribe_to('adp.permission_check')
    def _on_permission_check(*, user=None, user_id=None, user_public_id=None,
                             module_name, permission_key, **_):
        ...

Пример потребителя (любой другой модуль):

    from src.core.integrations import bridge

    entity_ids = bridge.call(
        '<name>.get_user_entity_ids',
        user=request.user,
        user_public_id=str(request.user.public_id),
        default=[],
    )

    if not bridge.has('<name>.get_entity'):
        return Response(...)

Если модуль-провайдер не подключён, bridge.call возвращает default
(тихо, без исключений), а bridge.emit — пустой список.

Подробнее о транспортах (in-process / HTTP / Celery) —
см. transports/base.py.
"""

from .bridge import ModuleBridge, bridge
from .exceptions import BridgeContractError, BridgeError, DuplicateProvider
from .isolation import BridgeIsolationError, BridgeIsolationWarning
from .transports import (
    EventBus,
    HttpTransport,
    LocalEventBus,
    LocalTransport,
    RedisEventBus,
    Transport,
)

__all__ = [
    'ModuleBridge',
    'bridge',
    'BridgeError',
    'BridgeContractError',
    'DuplicateProvider',
    'BridgeIsolationError',
    'BridgeIsolationWarning',
    'Transport',
    'EventBus',
    'LocalTransport',
    'LocalEventBus',
    'HttpTransport',
    'RedisEventBus',
]
