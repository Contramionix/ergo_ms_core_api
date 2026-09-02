# -*- coding: utf-8 -*-
"""Удаляет пункт меню OnlineUsersPanel («В сети»).

Ошибочно засевался из populate_core_menu / restore_admin_panel_menu;
отдельной страницы на клиенте нет — фильтр «В сети» живёт на UsersPanel.
"""

from django.db import migrations

# Не включать в цепочку restore_menu (одноразовая очистка; seed уже убран в 0001).
MENU_RESTORE_SKIP = True

ROUTE_NAME = 'OnlineUsersPanel'


def purge_online_users_panel_menu(apps, schema_editor):
    MenuItem = apps.get_model('cms_adp', 'MenuItem')
    MenuItem.objects.filter(route_name=ROUTE_NAME).delete()

    try:
        MenuLayoutPlacement = apps.get_model('cms_adp', 'MenuLayoutPlacement')
    except LookupError:
        MenuLayoutPlacement = None

    if MenuLayoutPlacement is not None:
        MenuLayoutPlacement.objects.filter(
            catalog_key__contains=f'::route::{ROUTE_NAME}',
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('cms_adp', '0063_ensure_menuseparator_access_acl'),
    ]

    operations = [
        migrations.RunPython(
            purge_online_users_panel_menu,
            migrations.RunPython.noop,
        ),
    ]
