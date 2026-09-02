# -*- coding: utf-8 -*-
"""Удаляет меню модуля, ошибочно засеянное из ядра при restore_menu."""

from django.db import migrations

# Исторический module_source в БД — менять нельзя (уже применённые установки).
MODULE_SOURCE = 'modules/neural_networks_hub'
ROLE_GROUP_TEACHER = 'Преподаватель (НН)'
ROLE_GROUP_STUDENT = 'Студент (НН)'


def remove_seeded_module_menu(apps, schema_editor):
    MenuItem = apps.get_model('cms_adp', 'MenuItem')
    MenuSeparator = apps.get_model('cms_adp', 'MenuSeparator')
    RoleGroup = apps.get_model('cms_adp', 'RoleGroup')

    MenuItem.objects.filter(module_source=MODULE_SOURCE).delete()
    if hasattr(MenuSeparator, 'module_source'):
        MenuSeparator.objects.filter(module_source=MODULE_SOURCE).delete()

    try:
        MenuLayoutPlacement = apps.get_model('cms_adp', 'MenuLayoutPlacement')
    except LookupError:
        MenuLayoutPlacement = None
    try:
        MenuSeparatorLayout = apps.get_model('cms_adp', 'MenuSeparatorLayout')
    except LookupError:
        MenuSeparatorLayout = None

    prefix = f'{MODULE_SOURCE}::'
    if MenuLayoutPlacement is not None:
        MenuLayoutPlacement.objects.filter(catalog_key__startswith=prefix).delete()
    if MenuSeparatorLayout is not None:
        MenuSeparatorLayout.objects.filter(catalog_key__startswith=prefix).delete()

    RoleGroup.objects.filter(
        name__in=[ROLE_GROUP_TEACHER, ROLE_GROUP_STUDENT],
    ).delete()

    from src.core.cms.adp.menu.migration_utils import reanchor_modules_section_separator

    reanchor_modules_section_separator(apps)


class Migration(migrations.Migration):

    dependencies = [
        ('cms_adp', '0057_emailconfirmationcode_failed_attempts'),
    ]

    operations = [
        migrations.RunPython(
            remove_seeded_module_menu,
            migrations.RunPython.noop,
        ),
    ]
