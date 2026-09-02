# -*- coding: utf-8 -*-
"""
Идемпотентный ремонт ACL MenuSeparator.

0056 добавляет is_admin_only / allowed_* (в БД — только если объектов ещё нет),
0059 пустая (операции перенесены в 0056).
На установках, где 0056/0059 уже были в django_migrations до появления AddField
в файле 0056, колонок и M2M-таблиц нет, а migrate их не повторяет.

Здесь только schema DB: state Django уже содержит поля с 0056.
"""

from django.db import migrations

from src.core.cms.adp.menu.menuseparator_acl_schema import (
    ensure_menuseparator_access_acl,
)

# Не включать в цепочку restore_menu (одноразовый schema-repair, не populate).
MENU_RESTORE_SKIP = True


class Migration(migrations.Migration):

    dependencies = [
        ('cms_adp', '0062_remove_policy_type_component'),
    ]

    operations = [
        migrations.RunPython(
            ensure_menuseparator_access_acl,
            migrations.RunPython.noop,
        ),
    ]
