from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='OutboxEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('public_id', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('event', models.CharField(db_index=True, max_length=255)),
                ('payload', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('published_at', models.DateTimeField(blank=True, null=True)),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
            ],
            options={
                'db_table': 'core_outbox_event',
                'ordering': ['created_at'],
            },
        ),
        migrations.CreateModel(
            name='InboxEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event', models.CharField(max_length=255)),
                ('idempotency_key', models.CharField(max_length=255)),
                ('payload', models.JSONField(default=dict)),
                ('received_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'core_inbox_event',
            },
        ),
        migrations.AddIndex(
            model_name='outboxevent',
            index=models.Index(fields=['published_at', 'created_at'], name='core_outbox_pub_created_idx'),
        ),
        migrations.AddConstraint(
            model_name='inboxevent',
            constraint=models.UniqueConstraint(
                fields=('event', 'idempotency_key'),
                name='core_inbox_event_idem_uniq',
            ),
        ),
    ]
