from __future__ import annotations

import logging
from uuid import UUID

from django.contrib.contenttypes.models import ContentType

from src.core.messenger.utils import get_content_type

logger = logging.getLogger(__name__)


def _lookup_object(model_class, object_ref):
    if object_ref in (None, ''):
        return None

    raw = str(object_ref).strip()
    if not raw:
        return None

    if raw.isdigit():
        try:
            return model_class.objects.get(pk=int(raw))
        except (TypeError, ValueError, model_class.DoesNotExist):
            return None

    try:
        public_id = UUID(raw)
    except (TypeError, ValueError):
        return None

    if not hasattr(model_class, 'public_id'):
        return None

    try:
        return model_class.objects.get(public_id=public_id)
    except (TypeError, ValueError, model_class.DoesNotExist):
        return None


def get_messenger_object(content_type_name: str, object_ref):
    """Объект чата по content_type и pk или public_id."""
    if not content_type_name:
        return None

    try:
        ct = get_content_type(content_type_name)
    except Exception:
        return None

    if ct is None:
        return None

    model_class = ct.model_class()
    if model_class is None:
        return None

    return _lookup_object(model_class, object_ref)


def resolve_messenger_object_pk(content_type_name: str, object_ref) -> int | None:
    obj = get_messenger_object(content_type_name, object_ref)
    return None if obj is None else obj.pk


def has_messenger_access(user, content_type_name: str, object_id) -> bool:
    """Проверка доступа к чату по content_type и object_id.

    ``object_id`` — pk или ``public_id``. Модели с обсуждениями обязаны
    реализовать ``has_messenger_access(self, user) -> bool``.
    Без метода — отказ (deny-by-default).
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return False

    obj = get_messenger_object(content_type_name, object_id)
    if obj is None:
        return False

    checker = getattr(obj, 'has_messenger_access', None)
    if not callable(checker):
        return False

    try:
        return bool(checker(user))
    except Exception:
        logger.exception(
            'has_messenger_access failed for %s ref=%s',
            content_type_name,
            object_id,
        )
        return False


def get_content_type_name(content_type: ContentType) -> str:
    return f'{content_type.app_label}.{content_type.model}'
