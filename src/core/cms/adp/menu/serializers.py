"""
Сериализаторы для управления меню.
"""

from django.utils.translation import gettext as _
from rest_framework import serializers
from rest_framework.serializers import (
    ModelSerializer,
    CharField,
    ValidationError,
    Serializer,
    ListField,
    UUIDField,
)

from src.core.cms.adp.serializers import RoleSerializer, RoleGroupSerializer
from .access import user_can_see_menu_item
from .models import MenuItem, MenuSeparator, MenuAccessLog


class MenuItemChildSerializer(ModelSerializer):
    """Сериализатор для дочерних элементов меню (без рекурсии)"""
    id = UUIDField(source='public_id', read_only=True)

    class Meta:
        model = MenuItem
        fields = [
            'id', 'catalog_key', 'name', 'route_name', 'icon', 'item_type',
            'page', 'order', 'is_active',
        ]


class MenuItemSerializer(ModelSerializer):
    """Сериализатор для элементов меню"""
    id = UUIDField(source='public_id', read_only=True)
    children = MenuItemChildSerializer(many=True, read_only=True)
    parent = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=MenuItem.objects.all(),
        allow_null=True,
        required=False,
    )
    parent_name = CharField(source='parent.name', read_only=True, allow_null=True)
    allowed_roles_data = RoleSerializer(source='allowed_roles', many=True, read_only=True)
    allowed_role_groups_data = RoleGroupSerializer(source='allowed_role_groups', many=True, read_only=True)

    class Meta:
        model = MenuItem
        fields = [
            'id', 'catalog_key', 'name', 'route_name', 'icon', 'item_type',
            'page', 'external_url', 'parent', 'parent_name',
            'order', 'is_active', 'is_admin_only',
            'allowed_roles', 'allowed_roles_data',
            'allowed_role_groups', 'allowed_role_groups_data',
            'module_source', 'children', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'catalog_key', 'created_at', 'updated_at', 'children']


class MenuItemTreeSerializer(ModelSerializer):
    """Рекурсивный сериализатор для дерева меню"""
    id = UUIDField(source='public_id', read_only=True)
    children = serializers.SerializerMethodField()

    class Meta:
        model = MenuItem
        fields = [
            'id', 'catalog_key', 'name', 'route_name', 'icon', 'item_type',
            'page', 'external_url', 'order', 'children',
        ]

    def get_children(self, obj):
        user = self.context.get('user')
        children = obj.children.filter(is_active=True).order_by('order')

        if user:
            children = self._filter_by_access(children, user)

        data = MenuItemTreeSerializer(children, many=True, context=self.context).data
        return self._prune_empty_folder_nodes(data)

    @staticmethod
    def _is_leaf_without_route_but_visible(node):
        """Лист без route_name: offcanvas (page) и external (url) — не пустые папки."""
        item_type = node.get('item_type')
        if item_type == 'offcanvas' and node.get('page'):
            return True
        if item_type == 'external' and node.get('external_url'):
            return True
        return False

    def _prune_empty_folder_nodes(self, nodes):
        """Убирает папки-маршруты без route_name, у которых не осталось детей после фильтрации."""
        if not nodes:
            return []
        result = []
        for node in nodes:
            raw_children = node.get('children') or []
            if raw_children:
                raw_children = self._prune_empty_folder_nodes(raw_children)
            if raw_children:
                node = dict(node)
                node['children'] = raw_children
            route_name = node.get('route_name')
            if not route_name and not raw_children:
                if self._is_leaf_without_route_but_visible(node):
                    result.append(node)
                continue
            result.append(node)
        return result

    def _filter_by_access(self, items, user):
        """Фильтрует элементы меню по правам доступа пользователя."""
        filtered_ids = []
        for item in items:
            if user_can_see_menu_item(item, user):
                filtered_ids.append(item.id)

        return items.filter(id__in=filtered_ids)


class MenuSeparatorSerializer(ModelSerializer):
    """Сериализатор для разделителей меню"""
    id = UUIDField(source='public_id', read_only=True)
    allowed_roles_data = RoleSerializer(source='allowed_roles', many=True, read_only=True)
    allowed_role_groups_data = RoleGroupSerializer(
        source='allowed_role_groups', many=True, read_only=True,
    )

    class Meta:
        model = MenuSeparator
        fields = [
            'id', 'catalog_key', 'module_source', 'name',
            'before_order', 'before_catalog_key', 'is_active', 'is_admin_only',
            'allowed_roles', 'allowed_roles_data',
            'allowed_role_groups', 'allowed_role_groups_data',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'catalog_key', 'created_at', 'updated_at']


class UserMenuSerializer(Serializer):
    """Сериализатор для получения меню пользователя"""
    menu_items = MenuItemTreeSerializer(many=True, read_only=True)
    separators = MenuSeparatorSerializer(many=True, read_only=True)


class MenuItemCreateSerializer(ModelSerializer):
    """Сериализатор для создания элемента меню"""
    parent = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=MenuItem.objects.all(),
        allow_null=True,
        required=False,
    )

    class Meta:
        model = MenuItem
        fields = [
            'name', 'route_name', 'icon', 'item_type',
            'page', 'external_url', 'parent', 'order',
            'is_active', 'is_admin_only', 'allowed_roles',
            'allowed_role_groups', 'module_source',
        ]

    def validate(self, attrs):
        item_type = attrs.get('item_type', 'route')

        if item_type == 'offcanvas' and not attrs.get('page'):
            raise ValidationError({'page': _('Страница обязательна для типа "offcanvas"')})

        if item_type == 'external' and not attrs.get('external_url'):
            raise ValidationError({'external_url': _('URL обязателен для типа "external"')})

        return attrs


class MenuItemUpdateSerializer(ModelSerializer):
    """Сериализатор для обновления элемента меню"""
    parent = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=MenuItem.objects.all(),
        allow_null=True,
        required=False,
    )

    class Meta:
        model = MenuItem
        fields = [
            'name', 'route_name', 'icon', 'item_type',
            'page', 'external_url', 'parent', 'order',
            'is_active', 'is_admin_only', 'allowed_roles',
            'allowed_role_groups', 'module_source',
        ]


class MenuItemReorderSerializer(Serializer):
    """Сериализатор для изменения порядка элементов меню (id = public_id)."""
    items = ListField(
        child=serializers.DictField(),
        help_text='Список объектов с id (public_id), order и опционально parent_id (public_id)',
    )

    def validate_items(self, value):
        uuid_field = UUIDField()
        for item in value:
            if 'id' not in item or 'order' not in item:
                raise ValidationError(_('Каждый элемент должен содержать id и order'))
            try:
                uuid_field.to_internal_value(item['id'])
            except Exception as exc:
                raise ValidationError(
                    _('id должен быть UUID: %(value)s') % {'value': item.get('id')}
                ) from exc
            if 'parent_id' in item and item['parent_id'] is not None:
                try:
                    uuid_field.to_internal_value(item['parent_id'])
                except Exception as exc:
                    raise ValidationError(
                        _('parent_id должен быть UUID или null для элемента %(value)s')
                        % {'value': item.get('id')}
                    ) from exc
        return value


class MenuAccessLogSerializer(ModelSerializer):
    """Сериализатор для логов доступа к меню"""
    username = CharField(source='user.username', read_only=True)
    menu_item_name = CharField(source='menu_item.name', read_only=True)

    class Meta:
        model = MenuAccessLog
        fields = [
            'id', 'user', 'username', 'menu_item',
            'menu_item_name', 'accessed_at',
        ]
        read_only_fields = ['id', 'accessed_at']
