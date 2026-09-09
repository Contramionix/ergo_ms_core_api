from django.db import migrations


def retarget_auth_user_fks(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != 'postgresql':
        return
    from src.core.utils.database.schema_move import (
        retarget_public_auth_user_foreign_keys,
    )

    retarget_public_auth_user_foreign_keys(connection)


class Migration(migrations.Migration):

    dependencies = [
        ('cms_adp', '0064_purge_online_users_panel_menu'),
    ]

    operations = [
        migrations.RunPython(retarget_auth_user_fks, migrations.RunPython.noop),
    ]
