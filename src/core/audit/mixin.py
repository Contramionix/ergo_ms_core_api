"""DRF-миксин автоматического аудита CRUD.

Подмешивается к ViewSet — и create/update/destroy пишутся в журнал сами,
с автоматическим diff изменённых полей. Модулю не нужно вызывать audit.record
вручную для типовых операций.

Пример:

    class CourseViewSet(AuditedModelMixin, BaseModelViewSet):
        audit_module = 'my_module'
        # всё; при желании — audit_action_map / audit_entity_type
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.utils.encoding import force_str

from .service import AuditService

logger = logging.getLogger('core.audit')

_PRIMITIVES = (str, int, float, bool, type(None))


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, _PRIMITIVES):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    return force_str(value)


class AuditedModelMixin:
    """Автоаудит для DRF ModelViewSet.

    Атрибуты для настройки (все опциональны):
        audit_module: источник; по умолчанию app_label модели.
        audit_entity_type: тип объекта; по умолчанию имя модели в нижнем регистре.
        audit_action_map: {'create': 'x.created', 'update': ..., 'destroy': ...}.
        audit_enabled: выключатель.
    """

    audit_module: str = ''
    audit_entity_type: str = ''
    audit_action_map: dict = {}
    audit_severity: str = 'info'
    audit_enabled: bool = True

    def _audit_model(self):
        queryset = getattr(self, 'queryset', None)
        if queryset is not None:
            return queryset.model
        return None

    def _audit_source(self) -> str:
        if self.audit_module:
            return self.audit_module
        model = self._audit_model()
        return model._meta.app_label if model is not None else ''

    def _audit_entity_type(self) -> str:
        if self.audit_entity_type:
            return self.audit_entity_type
        model = self._audit_model()
        return model._meta.model_name if model is not None else ''

    def _audit_action(self, verb: str) -> str:
        if verb in self.audit_action_map:
            return self.audit_action_map[verb]
        suffix = {'create': 'created', 'update': 'updated', 'destroy': 'deleted'}.get(verb, verb)
        return f'{self._audit_entity_type()}.{suffix}'

    def _audit_entity(self, instance) -> dict:
        # ref — непредсказуемая публичная ссылка (public_id/UUID), не pk БД.
        ref = ''
        for attr in ('public_id', 'uuid', 'ref'):
            value = getattr(instance, attr, None)
            if value:
                ref = force_str(value)
                break
        return {
            'type': self._audit_entity_type(),
            'ref': ref,
            'label': force_str(instance)[:255],
        }

    def _tracked_fields(self, serializer) -> list[str]:
        data = getattr(serializer, 'validated_data', None) or {}
        return [f for f in data.keys()]

    def _snapshot(self, instance, fields: list[str]) -> dict:
        snap = {}
        for field in fields:
            try:
                snap[field] = _to_jsonable(getattr(instance, field))
            except Exception:
                snap[field] = None
        return snap

    def _build_changes(self, old: dict, new: dict) -> list[dict]:
        changes = []
        keys = set(old) | set(new)
        for field in sorted(keys):
            old_value = old.get(field)
            new_value = new.get(field)
            if old_value == new_value:
                continue
            changes.append({'field': field, 'old': old_value, 'new': new_value})
        return changes

    def _record(self, verb: str, instance, changes: list | None) -> None:
        if not self.audit_enabled:
            return
        try:
            AuditService.record(
                action=self._audit_action(verb),
                source_module=self._audit_source(),
                request=getattr(self, 'request', None),
                entity=self._audit_entity(instance),
                changes=changes or None,
                severity=self.audit_severity,
            )
        except Exception:
            logger.exception('AuditedModelMixin: сбой аудита для %s', verb)

    def perform_create(self, serializer):
        fields = self._tracked_fields(serializer)
        instance = serializer.save()
        new = self._snapshot(instance, fields)
        changes = [{'field': f, 'old': None, 'new': new.get(f)} for f in fields]
        self._record('create', instance, changes)
        return instance

    def perform_update(self, serializer):
        fields = self._tracked_fields(serializer)
        old = self._snapshot(serializer.instance, fields)
        instance = serializer.save()
        new = self._snapshot(instance, fields)
        self._record('update', instance, self._build_changes(old, new))
        return instance

    def perform_destroy(self, instance):
        entity = self._audit_entity(instance)
        source = self._audit_source()
        action = self._audit_action('destroy')
        request = getattr(self, 'request', None)
        super().perform_destroy(instance)
        AuditService.record(
            action=action,
            source_module=source,
            request=request,
            entity=entity,
            severity=self.audit_severity,
        )
