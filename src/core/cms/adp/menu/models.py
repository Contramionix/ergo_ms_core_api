"""
Модели для управления боковым меню.

Каталог: MenuItem / MenuSeparator (пересоздаётся миграциями и restore).
Layout: MenuLayoutPlacement / MenuSeparatorLayout (переживает sync, накатывается на каталог).
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Max

from src.core.cms.adp.models import Role, RoleGroup


class MenuItem(models.Model):
    """
    Элемент бокового меню (каталог).
    Иерархия и порядок в рантайме материализуются из MenuLayoutPlacement.
    """
    ITEM_TYPES = [
        ('route', 'Маршрут Vue'),
        ('offcanvas', 'Боковая панель'),
        ('external', 'Внешняя ссылка'),
    ]

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        verbose_name='public id',
    )

    catalog_key = models.CharField(
        max_length=512,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        verbose_name='Ключ каталога',
        help_text='Стабильный ключ seed/админа; не зависит от PK',
    )

    name = models.CharField(max_length=100, verbose_name='Название')
    route_name = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='Имя маршрута Vue',
        help_text='Имя маршрута из Vue Router (например: User, Settings, BI)',
    )
    icon = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name='Иконка',
        help_text='Название иконки из Lucide (например: CircleUserRound, UserCog)',
    )
    item_type = models.CharField(
        max_length=20,
        choices=ITEM_TYPES,
        default='route',
        verbose_name='Тип элемента',
    )

    page = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name='Страница боковой панели',
        help_text='Для BI модуля: datasets, connections, charts, dashboards',
    )

    external_url = models.URLField(
        blank=True,
        null=True,
        verbose_name='Внешняя ссылка',
    )

    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children',
        verbose_name='Родительский элемент',
    )

    order = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Порядок',
        help_text='Эффективный порядок (материализация layout)',
    )

    is_active = models.BooleanField(default=True, verbose_name='Активен')
    is_admin_only = models.BooleanField(
        default=False,
        verbose_name='Только для администраторов',
    )

    allowed_roles = models.ManyToManyField(
        Role,
        blank=True,
        related_name='menu_items',
        verbose_name='Разрешённые роли',
        help_text='Если не выбрано ни одной роли, доступно всем',
    )

    allowed_role_groups = models.ManyToManyField(
        RoleGroup,
        blank=True,
        related_name='menu_items',
        verbose_name='Разрешённые ролевые группы',
    )

    module_source = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='Модуль-источник',
        help_text='Путь к модулю (core/… или modules/<name>)',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'cms_adp'
        verbose_name = 'Элемент меню'
        verbose_name_plural = 'Элементы меню'
        ordering = ['order', 'name']

    def __str__(self):
        if self.parent:
            return f'{self.parent.name} > {self.name}'
        return self.name

    def save(self, *args, **kwargs):
        if self.order is None:
            max_order = MenuItem.objects.filter(
                parent=self.parent
            ).aggregate(Max('order'))['order__max']
            self.order = (max_order or 0) + 10
        super().save(*args, **kwargs)

    @classmethod
    def get_next_order(cls, parent=None):
        """Возвращает следующий порядок для указанного родителя."""
        max_order = cls.objects.filter(parent=parent).aggregate(Max('order'))['order__max']
        return (max_order or 0) + 10

    def get_children_tree(self):
        """Рекурсивно получает все дочерние элементы"""
        children = []
        for child in self.children.filter(is_active=True).order_by('order'):
            child_data = {
                'id': str(child.public_id),
                'name': child.name,
                'route_name': child.route_name,
                'icon': child.icon,
                'item_type': child.item_type,
                'page': child.page,
                'order': child.order,
                'catalog_key': child.catalog_key,
                'children': child.get_children_tree(),
            }
            children.append(child_data)
        return children


class MenuLayoutPlacement(models.Model):
    """
    Layout пункта меню: parent / order / is_active.
    Переживает restore и clear_module_items; ключ — catalog_key.
    """
    catalog_key = models.CharField(
        max_length=512,
        unique=True,
        db_index=True,
        verbose_name='Ключ каталога',
    )
    parent_catalog_key = models.CharField(
        max_length=512,
        blank=True,
        null=True,
        db_index=True,
        verbose_name='Ключ родителя',
    )
    order = models.PositiveIntegerField(default=0, verbose_name='Порядок')
    is_active = models.BooleanField(default=True, verbose_name='Активен')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'cms_adp'
        verbose_name = 'Размещение пункта меню'
        verbose_name_plural = 'Размещения пунктов меню'
        ordering = ['order', 'catalog_key']

    def __str__(self):
        return f'{self.catalog_key} @ {self.order}'


class MenuSeparator(models.Model):
    """
    Разделитель в меню (каталог).
    Якорь before_catalog_key предпочтительнее числового before_order.
    """
    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        verbose_name='public id',
    )
    catalog_key = models.CharField(
        max_length=512,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        verbose_name='Ключ каталога',
    )
    module_source = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='Модуль-источник',
    )
    name = models.CharField(max_length=100, verbose_name='Название разделителя')
    before_order = models.PositiveIntegerField(
        verbose_name='Перед порядком',
        help_text='Запасной вариант: перед элементами с этим порядком',
    )
    before_catalog_key = models.CharField(
        max_length=512,
        blank=True,
        null=True,
        db_index=True,
        verbose_name='Перед пунктом (ключ каталога)',
        help_text='Якорь: разделитель перед пунктом с этим catalog_key',
    )

    is_active = models.BooleanField(default=True, verbose_name='Активен')
    is_admin_only = models.BooleanField(
        default=False,
        verbose_name='Только для администраторов',
    )

    allowed_roles = models.ManyToManyField(
        Role,
        blank=True,
        related_name='menu_separators',
        verbose_name='Разрешённые роли',
        help_text='Если не выбрано ни одной роли, доступно всем',
    )

    allowed_role_groups = models.ManyToManyField(
        RoleGroup,
        blank=True,
        related_name='menu_separators',
        verbose_name='Разрешённые ролевые группы',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'cms_adp'
        verbose_name = 'Разделитель меню'
        verbose_name_plural = 'Разделители меню'
        ordering = ['before_order']

    def __str__(self):
        anchor = self.before_catalog_key or self.before_order
        return f'{self.name} (перед {anchor})'


class MenuSeparatorLayout(models.Model):
    """Layout разделителя — переживает restore."""
    catalog_key = models.CharField(
        max_length=512,
        unique=True,
        db_index=True,
        verbose_name='Ключ каталога',
    )
    name = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='Название',
    )
    before_catalog_key = models.CharField(
        max_length=512,
        blank=True,
        null=True,
        verbose_name='Перед пунктом',
    )
    before_order = models.PositiveIntegerField(default=0, verbose_name='Перед порядком')
    is_active = models.BooleanField(default=True, verbose_name='Активен')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'cms_adp'
        verbose_name = 'Размещение разделителя'
        verbose_name_plural = 'Размещения разделителей'
        ordering = ['before_order', 'catalog_key']

    def __str__(self):
        return f'{self.catalog_key} → {self.before_catalog_key or self.before_order}'


class MenuAccessLog(models.Model):
    """
    Лог доступа к элементам меню.
    Для аналитики и аудита.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='menu_access_logs',
        verbose_name='Пользователь',
    )
    menu_item = models.ForeignKey(
        MenuItem,
        on_delete=models.CASCADE,
        related_name='access_logs',
        verbose_name='Элемент меню',
    )
    accessed_at = models.DateTimeField(auto_now_add=True, verbose_name='Время доступа')

    class Meta:
        app_label = 'cms_adp'
        verbose_name = 'Лог доступа к меню'
        verbose_name_plural = 'Логи доступа к меню'
        ordering = ['-accessed_at']

    def __str__(self):
        return f'{self.user.username} -> {self.menu_item.name} ({self.accessed_at})'
