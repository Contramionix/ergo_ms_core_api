# -*- coding: utf-8 -*-
"""
Утилиты для миграций меню.

Использование в миграциях:
    from src.core.cms.adp.menu.migration_utils import MenuMigrationHelper

    def populate_menu(apps, schema_editor):
        helper = MenuMigrationHelper(apps, 'modules/<name>')

        # clear удаляет только каталог модуля; layout (order/parent) сохраняется
        # и накатывается на пункты с тем же catalog_key при create_*.
        helper.clear_module_items()

        root = helper.create_group('Мой модуль', 'MyModule', icon='Folder')
        helper.create_route('Главная', 'MyModuleDashboard', parent=root, icon='Home')
"""

from django.db.models import Max

from .catalog_keys import build_item_catalog_key, build_separator_catalog_key

# Совпадает с разделителем «Модули» в populate_core_menu (before_order=20).
CORE_MODULES_SECTION_ORDER = 20
CORE_MODULES_SEPARATOR_NAME = 'Модули'


def _set_root_item_order(MenuItem, MenuLayoutPlacement, item, order: int) -> bool:
    if item.order == order:
        return False
    item.order = order
    item.save(update_fields=['order'])
    if MenuLayoutPlacement is not None and getattr(item, 'catalog_key', None):
        MenuLayoutPlacement.objects.filter(catalog_key=item.catalog_key).update(order=order)
    return True


def reanchor_modules_section_separator(apps=None) -> bool:
    """
    Якорь разделителя «Модули» — первый корневой пункт секции (order >= 20).
    Иначе при одинаковом order разделитель может встать после части модулей.
    """
    if apps is None:
        from django.apps import apps as django_apps
        apps = django_apps

    MenuItem = apps.get_model('cms_adp', 'MenuItem')
    MenuSeparator = apps.get_model('cms_adp', 'MenuSeparator')
    try:
        MenuSeparatorLayout = apps.get_model('cms_adp', 'MenuSeparatorLayout')
    except LookupError:
        MenuSeparatorLayout = None

    first = (
        MenuItem.objects.filter(
            parent__isnull=True,
            is_active=True,
            order__gte=CORE_MODULES_SECTION_ORDER,
        )
        .order_by('order', 'name')
        .first()
    )
    if first is None or not getattr(first, 'catalog_key', None):
        return False

    sep = (
        MenuSeparator.objects.filter(name=CORE_MODULES_SEPARATOR_NAME)
        .order_by('pk')
        .first()
    )
    if sep is None:
        return False

    changed = (
        sep.before_catalog_key != first.catalog_key
        or sep.before_order != CORE_MODULES_SECTION_ORDER
    )
    sep.before_catalog_key = first.catalog_key
    sep.before_order = CORE_MODULES_SECTION_ORDER
    if changed:
        sep.save(update_fields=['before_catalog_key', 'before_order'])

    if MenuSeparatorLayout is not None and getattr(sep, 'catalog_key', None):
        MenuSeparatorLayout.objects.filter(catalog_key=sep.catalog_key).update(
            before_catalog_key=first.catalog_key,
            before_order=CORE_MODULES_SECTION_ORDER,
        )
    return True


def ensure_modules_section_layout(apps=None) -> int:
    """
    Выравнивает корневые пункты modules/* в секцию «Модули»:
    уникальные order 20, 30, 40… и якорь разделителя на первый пункт.
    """
    if apps is None:
        from django.apps import apps as django_apps
        apps = django_apps

    MenuItem = apps.get_model('cms_adp', 'MenuItem')
    try:
        MenuLayoutPlacement = apps.get_model('cms_adp', 'MenuLayoutPlacement')
    except LookupError:
        MenuLayoutPlacement = None

    roots = list(
        MenuItem.objects.filter(
            parent__isnull=True,
            module_source__startswith='modules/',
        )
        .exclude(catalog_key__isnull=True)
        .exclude(catalog_key='')
        .order_by('order', 'name', 'pk')
    )
    updated = 0
    next_order = CORE_MODULES_SECTION_ORDER
    for item in roots:
        if _set_root_item_order(MenuItem, MenuLayoutPlacement, item, next_order):
            updated += 1
        next_order += MenuMigrationHelper.ORDER_STEP

    reanchor_modules_section_separator(apps)
    return updated


def align_module_root_menu_orders(apps, module_source: str) -> int:
    """
    Сдвигает корневые пункты модуля в секцию «Модули» и выравнивает всю секцию.
    """
    MenuItem = apps.get_model('cms_adp', 'MenuItem')
    try:
        MenuLayoutPlacement = apps.get_model('cms_adp', 'MenuLayoutPlacement')
    except LookupError:
        MenuLayoutPlacement = None

    roots = list(
        MenuItem.objects.filter(
            module_source=module_source,
            parent__isnull=True,
        )
        .exclude(catalog_key__isnull=True)
        .exclude(catalog_key='')
        .order_by('order', 'pk')
    )
    if not roots:
        return 0

    # Сначала подтянуть «провалившиеся» пункты, затем перенумеровать всю секцию.
    max_order = MenuItem.objects.filter(
        parent__isnull=True,
        order__gte=CORE_MODULES_SECTION_ORDER,
    ).aggregate(Max('order'))['order__max']
    next_order = max(max_order or 0, CORE_MODULES_SECTION_ORDER - MenuMigrationHelper.ORDER_STEP)

    updated = 0
    for item in roots:
        if (item.order or 0) < CORE_MODULES_SECTION_ORDER:
            next_order += MenuMigrationHelper.ORDER_STEP
            if _set_root_item_order(MenuItem, MenuLayoutPlacement, item, next_order):
                updated += 1

    updated += ensure_modules_section_layout(apps)
    return updated


class MenuMigrationHelper:
    """
    Хелпер для создания элементов меню в миграциях.
    Upsert по catalog_key: при повторном seed сохраняет layout из MenuLayoutPlacement.
    """

    ORDER_STEP = 10

    def __init__(self, apps, module_source: str):
        self.apps = apps
        self.MenuItem = apps.get_model('cms_adp', 'MenuItem')
        self.MenuSeparator = apps.get_model('cms_adp', 'MenuSeparator')
        self.module_source = module_source
        self._has_catalog_key = self._model_has_field(self.MenuItem, 'catalog_key')
        self._has_layout_models = (
            self._has_catalog_key and self._detect_layout_models(apps)
        )
        if self._has_layout_models:
            self.MenuLayoutPlacement = apps.get_model('cms_adp', 'MenuLayoutPlacement')
            self.MenuSeparatorLayout = apps.get_model('cms_adp', 'MenuSeparatorLayout')

    @staticmethod
    def _model_has_field(model, field_name: str) -> bool:
        try:
            model._meta.get_field(field_name)
            return True
        except Exception:
            return False

    @staticmethod
    def _detect_layout_models(apps) -> bool:
        try:
            apps.get_model('cms_adp', 'MenuLayoutPlacement')
            apps.get_model('cms_adp', 'MenuSeparatorLayout')
            return True
        except LookupError:
            return False

    def _get_next_order(self, parent=None) -> int:
        if self._is_module_root(parent):
            return self._get_next_order_in_modules_section()
        max_order = self.MenuItem.objects.filter(
            parent=parent
        ).aggregate(Max('order'))['order__max']
        return (max_order or 0) + self.ORDER_STEP

    def _is_module_root(self, parent) -> bool:
        return parent is None and self.module_source.startswith('modules/')

    def _get_next_order_in_modules_section(self) -> int:
        max_order = self.MenuItem.objects.filter(
            parent__isnull=True,
            order__gte=CORE_MODULES_SECTION_ORDER,
        ).aggregate(Max('order'))['order__max']
        if max_order is None:
            return CORE_MODULES_SECTION_ORDER
        return max_order + self.ORDER_STEP

    def _normalize_modules_section_order(
        self,
        order: int | None,
        parent,
        catalog_key: str | None = None,
    ) -> int | None:
        if not self._is_module_root(parent):
            return order
        if order is None or order < CORE_MODULES_SECTION_ORDER:
            return self._get_next_order_in_modules_section()

        conflict = self.MenuItem.objects.filter(parent__isnull=True, order=order)
        if catalog_key:
            conflict = conflict.exclude(catalog_key=catalog_key)
        if conflict.exists():
            return self._get_next_order_in_modules_section()
        return order

    def _parent_catalog_key(self, parent) -> str | None:
        if parent is None:
            return None
        return getattr(parent, 'catalog_key', None) or None

    def _item_catalog_key(
        self,
        *,
        item_type: str,
        route_name=None,
        page=None,
        external_url=None,
        name=None,
        parent=None,
    ) -> str:
        return build_item_catalog_key(
            self.module_source,
            item_type=item_type,
            route_name=route_name,
            page=page,
            external_url=external_url,
            name=name,
            parent_catalog_key=self._parent_catalog_key(parent),
        )

    def _get_placement(self, catalog_key: str):
        if not self._has_layout_models or not catalog_key:
            return None
        return self.MenuLayoutPlacement.objects.filter(catalog_key=catalog_key).first()

    def _ensure_item_placement(self, item, *, seed_order, seed_parent, seed_is_active):
        if not self._has_layout_models or not item.catalog_key:
            return
        existing = self.MenuLayoutPlacement.objects.filter(catalog_key=item.catalog_key).first()
        if existing:
            parent = None
            if existing.parent_catalog_key:
                parent = self.MenuItem.objects.filter(
                    catalog_key=existing.parent_catalog_key
                ).first()
            # Для корня modules/* seed_order уже нормализован (секция «Модули») —
            # не откатываем его старым placement.order.
            if (
                self._is_module_root(seed_parent)
                and seed_order is not None
                and existing.order != seed_order
            ):
                existing.order = seed_order
                existing.save(update_fields=['order'])
            item.parent = parent
            item.order = existing.order
            item.is_active = existing.is_active
            item.save(update_fields=['parent', 'order', 'is_active'])
            return

        parent_key = self._parent_catalog_key(seed_parent)
        self.MenuLayoutPlacement.objects.create(
            catalog_key=item.catalog_key,
            parent_catalog_key=parent_key,
            order=seed_order if seed_order is not None else (item.order or 0),
            is_active=seed_is_active,
        )

    def _ensure_separator_layout(self, sep, *, seed_before_order, seed_before_key, seed_is_active):
        if not self._has_layout_models or not sep.catalog_key:
            return
        existing = self.MenuSeparatorLayout.objects.filter(catalog_key=sep.catalog_key).first()
        if existing:
            sep.before_catalog_key = existing.before_catalog_key
            sep.before_order = existing.before_order
            sep.is_active = existing.is_active
            if existing.name:
                sep.name = existing.name
            sep.save(update_fields=['before_catalog_key', 'before_order', 'is_active', 'name'])
            return

        self.MenuSeparatorLayout.objects.create(
            catalog_key=sep.catalog_key,
            name=sep.name,
            before_catalog_key=seed_before_key,
            before_order=seed_before_order,
            is_active=seed_is_active,
        )

    def clear_module_items(self):
        """
        Удаляет каталог элементов этого модуля.
        Layout (MenuLayoutPlacement) не удаляется — при create_* накатится обратно.
        """
        self.MenuItem.objects.filter(module_source=self.module_source).delete()
        if hasattr(self.MenuSeparator, 'module_source'):
            self.MenuSeparator.objects.filter(module_source=self.module_source).delete()

    def create_item(
        self,
        name: str,
        item_type: str,
        route_name: str = None,
        icon: str = None,
        parent=None,
        page: str = None,
        external_url: str = None,
        is_active: bool = True,
        is_admin_only: bool = False,
        order: int = None,
    ):
        catalog_key = (
            self._item_catalog_key(
                item_type=item_type,
                route_name=route_name,
                page=page,
                external_url=external_url,
                name=name,
                parent=parent,
            )
            if self._has_catalog_key
            else None
        )

        existing = None
        if self._has_catalog_key and catalog_key:
            existing = self.MenuItem.objects.filter(catalog_key=catalog_key).first()
        placement = self._get_placement(catalog_key) if self._has_catalog_key else None

        if placement is not None:
            parent_obj = None
            if placement.parent_catalog_key:
                parent_obj = self.MenuItem.objects.filter(
                    catalog_key=placement.parent_catalog_key
                ).first()
            effective_parent = parent_obj
            effective_order = self._normalize_modules_section_order(
                placement.order, parent, catalog_key=catalog_key,
            )
            if effective_order != placement.order:
                placement.order = effective_order
                placement.save(update_fields=['order'])
            effective_active = placement.is_active
        else:
            effective_parent = parent
            raw_order = order if order is not None else self._get_next_order(parent)
            effective_order = self._normalize_modules_section_order(
                raw_order, parent, catalog_key=catalog_key,
            )
            effective_active = is_active

        fields = dict(
            name=name,
            route_name=route_name,
            icon=icon,
            item_type=item_type,
            page=page,
            external_url=external_url,
            parent=effective_parent,
            order=effective_order,
            is_active=effective_active,
            is_admin_only=is_admin_only,
            module_source=self.module_source,
        )
        if self._has_catalog_key:
            fields['catalog_key'] = catalog_key

        if existing is not None:
            for key, value in fields.items():
                setattr(existing, key, value)
            existing.save()
            item = existing
        else:
            item = self.MenuItem.objects.create(**fields)

        self._ensure_item_placement(
            item,
            seed_order=effective_order,
            seed_parent=parent,
            seed_is_active=is_active,
        )
        return item

    def create_route(
        self,
        name: str,
        route_name: str,
        parent=None,
        icon: str = None,
        is_active: bool = True,
        is_admin_only: bool = False,
        order: int = None,
    ):
        return self.create_item(
            name=name,
            item_type='route',
            route_name=route_name,
            icon=icon,
            parent=parent,
            is_active=is_active,
            is_admin_only=is_admin_only,
            order=order,
        )

    def create_group(
        self,
        name: str,
        route_name: str = None,
        parent=None,
        icon: str = None,
        is_active: bool = True,
        is_admin_only: bool = False,
        order: int = None,
    ):
        return self.create_item(
            name=name,
            item_type='route',
            route_name=route_name,
            icon=icon,
            parent=parent,
            is_active=is_active,
            is_admin_only=is_admin_only,
            order=order,
        )

    def create_offcanvas(
        self,
        name: str,
        page: str,
        parent=None,
        icon: str = None,
        is_active: bool = True,
        order: int = None,
    ):
        return self.create_item(
            name=name,
            item_type='offcanvas',
            icon=icon,
            page=page,
            parent=parent,
            is_active=is_active,
            order=order,
        )

    def create_external(
        self,
        name: str,
        url: str,
        parent=None,
        icon: str = None,
        is_active: bool = True,
        order: int = None,
    ):
        return self.create_item(
            name=name,
            item_type='external',
            icon=icon,
            external_url=url,
            parent=parent,
            is_active=is_active,
            order=order,
        )

    def create_separator(
        self,
        name: str,
        before_order: int,
        is_active: bool = True,
        before_catalog_key: str = None,
    ):
        catalog_key = build_separator_catalog_key(self.module_source, name)

        existing = None
        if catalog_key and hasattr(self.MenuSeparator, 'catalog_key'):
            existing = self.MenuSeparator.objects.filter(catalog_key=catalog_key).first()

        create_kwargs = dict(
            name=name,
            before_order=before_order,
            is_active=is_active,
        )
        if hasattr(self.MenuSeparator, 'catalog_key'):
            create_kwargs['catalog_key'] = catalog_key
        if hasattr(self.MenuSeparator, 'module_source'):
            create_kwargs['module_source'] = self.module_source
        if hasattr(self.MenuSeparator, 'before_catalog_key'):
            create_kwargs['before_catalog_key'] = before_catalog_key

        if existing is not None:
            for key, value in create_kwargs.items():
                setattr(existing, key, value)
            existing.save()
            sep = existing
        else:
            sep = self.MenuSeparator.objects.create(**create_kwargs)

        self._ensure_separator_layout(
            sep,
            seed_before_order=before_order,
            seed_before_key=before_catalog_key,
            seed_is_active=is_active,
        )
        return sep

    def create_routes_batch(
        self,
        items: list,
        parent=None,
        is_active: bool = True,
    ):
        created = []
        for item in items:
            if len(item) == 2:
                name, route_name = item
                icon = None
            else:
                name, route_name, icon = item

            created.append(self.create_route(
                name=name,
                route_name=route_name,
                icon=icon,
                parent=parent,
                is_active=is_active,
            ))
        return created

    def create_offcanvas_batch(
        self,
        items: list,
        parent=None,
        is_active: bool = True,
    ):
        created = []
        for item in items:
            if len(item) == 2:
                name, page = item
                icon = None
            else:
                name, page, icon = item

            created.append(self.create_offcanvas(
                name=name,
                page=page,
                icon=icon,
                parent=parent,
                is_active=is_active,
            ))
        return created
