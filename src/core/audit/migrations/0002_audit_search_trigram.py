from django.contrib.postgres.indexes import GinIndex
from django.db import migrations

from src.core.utils.database.pg_trgm import (
    ensure_pg_trgm_backward,
    ensure_pg_trgm_forward,
)


class Migration(migrations.Migration):

    dependencies = [
        ('core_audit', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(ensure_pg_trgm_forward, ensure_pg_trgm_backward),
        migrations.AddIndex(
            model_name='auditevent',
            index=GinIndex(
                fields=['actor_label'],
                name='audit_actor_label_trgm',
                opclasses=['gin_trgm_ops'],
            ),
        ),
        migrations.AddIndex(
            model_name='auditevent',
            index=GinIndex(
                fields=['entity_label'],
                name='audit_entity_label_trgm',
                opclasses=['gin_trgm_ops'],
            ),
        ),
    ]
