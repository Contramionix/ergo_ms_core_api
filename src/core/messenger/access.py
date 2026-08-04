from __future__ import annotations

import logging

from django.contrib.contenttypes.models import ContentType

from src.core.messenger.utils import get_content_type

logger = logging.getLogger(__name__)


def has_messenger_access(user, content_type_name: str, object_id: int) -> bool:
    """Проверка доступа к чату по content_type и object_id.

    Модели с обсуждениями обязаны реализовать
    ``has_messenger_access(self, user) -> bool``. Без метода — отказ (deny-by-default).
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return False

    try:
        ct = get_content_type(content_type_name)
    except Exception:
        return False

    if ct is None:
        return False

    model_class = ct.model_class()
    if model_class is None:
        return False

    try:
        obj = model_class.objects.get(pk=object_id)
    except model_class.DoesNotExist:
        return False

    checker = getattr(obj, 'has_messenger_access', None)
    if not callable(checker):
        return False

    try:
        return bool(checker(user))
    except Exception:
        logger.exception(
            'has_messenger_access failed for %s pk=%s',
            content_type_name,
            object_id,
        )
        return False


def get_content_type_name(content_type: ContentType) -> str:
    return f'{content_type.app_label}.{content_type.model}'
