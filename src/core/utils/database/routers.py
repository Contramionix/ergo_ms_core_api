"""Маршрутизация моделей по alias из databases.yaml (уровень 3).

Секция YAML может содержать ``module: <имя_папки>``. Тогда app_label
этого модуля пишется/читается в указанный alias. Без поля — ``default``.
Имена модулей в коде ядра не зашиты.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger('utils.database.router')


def _alias_by_app_label() -> dict[str, str]:
    from django.conf import settings

    mapping = getattr(settings, 'MODULE_DATABASE_ALIASES', None)
    if isinstance(mapping, dict) and mapping:
        return {str(k): str(v) for k, v in mapping.items()}
    return {}


class ModuleDatabaseRouter:
    """Router: app_label модуля → alias БД, если задан MODULE_DATABASE_ALIASES."""

    def _alias(self, model: Any) -> str | None:
        app_label = getattr(getattr(model, '_meta', None), 'app_label', '') or ''
        return _alias_by_app_label().get(app_label)

    def db_for_read(self, model, **_hints):
        return self._alias(model)

    def db_for_write(self, model, **_hints):
        return self._alias(model)

    def allow_relation(self, obj1, obj2, **_hints):
        left = self._alias(obj1)
        right = self._alias(obj2)
        if left is None and right is None:
            return None
        if left == right:
            return True
        return False

    def allow_migrate(self, db, app_label, **_hints):
        mapping = _alias_by_app_label()
        target = mapping.get(app_label)
        if target is None:
            if db == 'default':
                return None
            if db in mapping.values():
                return False
            return None
        return db == target
