from rest_framework import serializers

from . import catalog
from .models import AuditEvent


class RecordUndoSerializer(serializers.Serializer):
    """Тело POST record-undo от клиентского reportUndo."""

    kind = serializers.CharField(max_length=128)
    label = serializers.CharField(max_length=255)
    entity_label = serializers.CharField(max_length=255, required=False, allow_blank=True)
    entity_type = serializers.CharField(max_length=64, required=False, allow_blank=True)
    entity_ref = serializers.CharField(max_length=128, required=False, allow_blank=True)
    source_module = serializers.CharField(max_length=64, required=False, allow_blank=True)
    meta = serializers.DictField(required=False)


class _AuditCatalogMixin(serializers.Serializer):
    """Обогащение записи данными каталога действий."""

    actor_username = serializers.CharField(source='actor.username', default=None, read_only=True)
    actor_ref = serializers.SerializerMethodField()
    actor_first_name = serializers.SerializerMethodField()
    actor_last_name = serializers.SerializerMethodField()
    actor_middle_name = serializers.SerializerMethodField()
    action_label = serializers.SerializerMethodField()
    module_label = serializers.SerializerMethodField()
    category_label = serializers.SerializerMethodField()
    icon = serializers.SerializerMethodField()

    def _catalog(self):
        cache = getattr(self, '_catalog_cache', None)
        if cache is None:
            cache = catalog.get_catalog()
            self._catalog_cache = cache
        return cache

    def _section(self, obj):
        return self._catalog().get(obj.source_module or '')

    def _spec(self, obj):
        return catalog.get_action_spec(obj.source_module or '', obj.action or '')

    def get_actor_ref(self, obj):
        actor = obj.actor
        if actor is None:
            return None
        public_id = getattr(actor, 'public_id', None)
        return str(public_id) if public_id else None

    def get_actor_first_name(self, obj):
        actor = obj.actor
        return (actor.first_name or '') if actor else ''

    def get_actor_last_name(self, obj):
        actor = obj.actor
        return (actor.last_name or '') if actor else ''

    def get_actor_middle_name(self, obj):
        actor = obj.actor
        return (getattr(actor, 'middle_name', None) or '') if actor else ''

    def get_action_label(self, obj):
        spec = self._spec(obj)
        return spec['label'] if spec else obj.action

    def get_module_label(self, obj):
        section = self._section(obj)
        return section['module_label'] if section else obj.source_module

    def get_category_label(self, obj):
        spec = self._spec(obj)
        return spec['category_label'] if spec else ''

    def get_icon(self, obj):
        spec = self._spec(obj)
        return spec['icon'] if spec else ''


class AuditEventListSerializer(_AuditCatalogMixin, serializers.ModelSerializer):
    """Облегчённая запись для таблицы журнала (без changes/meta/user_agent/scope).

    Поля урезаны под колонки списка: меньше JSON на медленной сети.
    """

    ip_location = serializers.SerializerMethodField()

    class Meta:
        model = AuditEvent
        ref_name = 'CoreAuditEventList'
        fields = (
            'id',
            'created_at',
            'actor_ref',
            'actor_first_name',
            'actor_last_name',
            'actor_middle_name',
            'actor_label',
            'module_label',
            'action_label',
            'icon',
            'severity',
            'entity_label',
            'ip_address',
            'ip_location',
        )
        read_only_fields = fields

    def get_ip_location(self, obj):
        from src.core.utils.geoip import format_ip_location

        return format_ip_location(obj.ip_address)



class AuditEventDetailSerializer(_AuditCatalogMixin, serializers.ModelSerializer):
    """Полная запись для модалки деталей."""

    ip_location = serializers.SerializerMethodField()

    class Meta:
        model = AuditEvent
        ref_name = 'CoreAuditEvent'
        fields = (
            'id',
            'created_at',
            'actor_ref',
            'actor_first_name',
            'actor_last_name',
            'actor_middle_name',
            'actor_label',
            'actor_username',
            'source_module',
            'module_label',
            'action',
            'action_label',
            'category_label',
            'icon',
            'severity',
            'entity_type',
            'entity_ref',
            'entity_label',
            'scope',
            'ip_address',
            'ip_location',
            'changes',
            'meta',
            'user_agent',
            'request_id',
        )
        read_only_fields = fields

    def get_ip_location(self, obj):
        from src.core.utils.geoip import format_ip_location

        return format_ip_location(obj.ip_address)
