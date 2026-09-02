# -*- coding: utf-8 -*-
"""
После 0051–0052 меню пустое; 0053 не может вызывать restore_menu до catalog_key.
0054 добавляет catalog_key/layout. Здесь — ACL разделителей + засев меню вне
schema-транзакции (иначе PostgreSQL: CREATE INDEX при отложенных trigger events
после массовых INSERT).

Поля is_admin_only / allowed_* для MenuSeparator нужны до call_command('restore_menu'):
команда использует живые модели, а не historical apps.

AddField в БД идемпотентны: atomic=False, повтор после сбоя на CREATE TABLE
не должен падать с already exists.
"""

from django.core.management import call_command
from django.db import migrations, models

from src.core.cms.adp.menu.menuseparator_acl_schema import (
    ensure_menuseparator_access_acl,
)

# Не включать в _discover_core_menu_migrations (иначе restore_menu вызовет сам себя).
MENU_RESTORE_ORCHESTRATOR = True


def restore_menu_if_core_missing(apps, schema_editor):
    MenuItem = apps.get_model('cms_adp', 'MenuItem')
    if MenuItem.objects.filter(module_source='core/cms').exists():
        return
    call_command('restore_menu')


class Migration(migrations.Migration):
    # Массовый seed + индексы в одной atomic-транзакции ломают PostgreSQL.
    atomic = False

    dependencies = [
        ('cms_adp', '0055_alter_menuitem_order_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='menuseparator',
                    name='allowed_role_groups',
                    field=models.ManyToManyField(
                        blank=True,
                        related_name='menu_separators',
                        to='cms_adp.rolegroup',
                        verbose_name='Разрешённые ролевые группы',
                    ),
                ),
                migrations.AddField(
                    model_name='menuseparator',
                    name='allowed_roles',
                    field=models.ManyToManyField(
                        blank=True,
                        help_text='Если не выбрано ни одной роли, доступно всем',
                        related_name='menu_separators',
                        to='cms_adp.role',
                        verbose_name='Разрешённые роли',
                    ),
                ),
                migrations.AddField(
                    model_name='menuseparator',
                    name='is_admin_only',
                    field=models.BooleanField(
                        default=False,
                        verbose_name='Только для администраторов',
                    ),
                ),
            ],
            database_operations=[
                migrations.RunPython(
                    ensure_menuseparator_access_acl,
                    migrations.RunPython.noop,
                ),
            ],
        ),
        migrations.RunPython(
            restore_menu_if_core_missing,
            migrations.RunPython.noop,
        ),
    ]
