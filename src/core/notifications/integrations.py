"""Регистрация публичного API уведомлений в ModuleBridge.

Модули создают уведомления только через `bridge.call(NOTIFICATIONS_CREATE, ...)`,
без прямого импорта моделей/сервиса этого приложения.
"""

from src.core.integrations import bridge
from src.core.integrations.module_contracts import NOTIFICATIONS_CREATE, NOTIFICATIONS_RECALL

from .services import NotificationService


@bridge.provide_op(NOTIFICATIONS_CREATE)
def _create_notification(
    *,
    recipient,
    title,
    body='',
    level='info',
    icon='',
    source_module='',
    event_key='',
    link_url=None,
    route=None,
    meta=None,
    actions=None,
    idempotency_key=None,
):
    """Создать уведомление и доставить его по всем зарегистрированным каналам.

    Параметры:
        recipient: User или его PK.
        title (str): заголовок уведомления (обязательно).
        body (str): текст.
        level (str): info|success|warning|error.
        icon (str): имя lucide-иконки (PascalCase), например 'BookOpen'.
            Если пусто — клиент применит fallback по source_module, затем по level.
        source_module (str): идентификатор модуля-источника, например 'my_module'.
        event_key (str): тип события внутри модуля, например 'course.review_required'.
        link_url (str|None): относительный путь без числовых pk (public_id/UUID).
        route (dict|None): {'name': '<RouteName>', 'params': {...}} — params только UUID
            или нечисловые строки; числовые pk отбрасываются при создании.
        meta (dict|None): произвольные данные.
        actions (list|None): кнопки [{id, label, style, handler}] для in_app.
        idempotency_key (str|None): защита от дублей при повторной доставке.

    Возвращает Notification или None при некорректных данных.
    """
    return NotificationService.dispatch(
        recipient=recipient,
        title=title,
        body=body,
        level=level,
        icon=icon,
        source_module=source_module,
        event_key=event_key,
        link_url=link_url,
        route=route,
        meta=meta,
        actions=actions,
        idempotency_key=idempotency_key,
    )


@bridge.provide_op(NOTIFICATIONS_RECALL)
def _recall_notification(*, idempotency_key=None, idempotency_keys=None, **_):
    """Отозвать неактуальные уведомления по idempotency_key.

    Параметры:
        idempotency_key (str|None): один ключ.
        idempotency_keys (iterable[str]|None): несколько ключей.

    Возвращает число отозванных записей.
    """
    return NotificationService.recall(
        idempotency_key=idempotency_key,
        idempotency_keys=idempotency_keys,
    )
