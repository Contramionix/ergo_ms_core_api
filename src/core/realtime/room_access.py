"""Доступ к комнате обсуждения по GenericForeignKey.

Хост комнаты (задача, диалог, вакансия) обязан объявить
``has_messenger_access(self, user) -> bool``. Нет метода — отказ.
Ядро не знает модели модулей и не импортирует ленту сообщений.
"""

from __future__ import annotations

import logging
from uuid import UUID

from django.contrib.contenttypes.models import ContentType

logger = logging.getLogger(__name__)


def _get_content_type(content_type_name: str):
    if not content_type_name:
        return None
    try:
        if '.' in content_type_name:
            app_label, model = content_type_name.split('.', 1)
            return ContentType.objects.get_by_natural_key(app_label, model)
        qs = ContentType.objects.filter(model=content_type_name)
        if qs.count() != 1:
            return None
        return qs.get()
    except Exception:
        return None


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


def get_room_object(content_type_name: str, object_ref):
    """Объект комнаты по content_type и pk или public_id."""
    ct = _get_content_type(content_type_name)
    if ct is None:
        return None
    model_class = ct.model_class()
    if model_class is None:
        return None
    return _lookup_object(model_class, object_ref)


def resolve_room_object_pk(content_type_name: str, object_ref) -> int | None:
    obj = get_room_object(content_type_name, object_ref)
    return None if obj is None else obj.pk


def _call_object_access(obj, method_name: str, user, *, content_type_name, object_id) -> bool:
    checker = getattr(obj, method_name, None)
    if not callable(checker):
        return False
    try:
        return bool(checker(user))
    except Exception:
        logger.exception(
            '%s failed for %s ref=%s',
            method_name,
            content_type_name,
            object_id,
        )
        return False


def has_messenger_access(user, content_type_name: str, object_id) -> bool:
    """Чтение комнаты. Нет метода на объекте — отказ."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    obj = get_room_object(content_type_name, object_id)
    if obj is None:
        return False
    return _call_object_access(
        obj,
        'has_messenger_access',
        user,
        content_type_name=content_type_name,
        object_id=object_id,
    )


def has_messenger_write_access(user, content_type_name: str, object_id) -> bool:
    """Право писать. Нет метода — как чтение комнаты."""
    if not has_messenger_access(user, content_type_name, object_id):
        return False
    obj = get_room_object(content_type_name, object_id)
    if obj is None:
        return False
    checker = getattr(obj, 'has_messenger_write_access', None)
    if not callable(checker):
        return True
    return _call_object_access(
        obj,
        'has_messenger_write_access',
        user,
        content_type_name=content_type_name,
        object_id=object_id,
    )


def has_messenger_attachment_access(user, content_type_name: str, object_id) -> bool:
    """Право вкладывать файлы. Нет метода — как чтение комнаты."""
    if not has_messenger_access(user, content_type_name, object_id):
        return False
    obj = get_room_object(content_type_name, object_id)
    if obj is None:
        return False
    checker = getattr(obj, 'has_messenger_attachment_access', None)
    if not callable(checker):
        return True
    return _call_object_access(
        obj,
        'has_messenger_attachment_access',
        user,
        content_type_name=content_type_name,
        object_id=object_id,
    )


def get_content_type_name(content_type: ContentType) -> str:
    return f'{content_type.app_label}.{content_type.model}'
