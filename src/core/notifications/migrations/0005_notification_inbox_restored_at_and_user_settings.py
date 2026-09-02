import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core_notifications', '0004_notification_lifecycle'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='inbox_restored_at',
            field=models.DateTimeField(
                blank=True,
                help_text='После «Из архива»: отсчёт автоархивации с этой даты, иначе с created_at',
                null=True,
                verbose_name='Восстановлено в inbox',
            ),
        ),
        migrations.AlterField(
            model_name='notification',
            name='deleted_at',
            field=models.DateTimeField(
                blank=True,
                help_text='Отозвано системой / модулем — скрыто из UI (не удаление пользователем)',
                null=True,
                verbose_name='Отозвано в',
            ),
        ),
        migrations.CreateModel(
            name='NotificationUserSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                (
                    'sidebar_activity_days',
                    models.PositiveSmallIntegerField(
                        default=3,
                        help_text='Сколько дней держать прочитанные уведомления в колокольчике (1–7)',
                        verbose_name='Активность в колокольчике (дней)',
                    ),
                ),
                (
                    'auto_archive_days',
                    models.PositiveSmallIntegerField(
                        default=14,
                        help_text='Через сколько дней прочитанные уходят в архив (7, 14, 30, 60, 90)',
                        verbose_name='Автоархив (дней)',
                    ),
                ),
                (
                    'user',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='core_notification_user_settings',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='Пользователь',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Настройки инбокса уведомлений',
                'verbose_name_plural': 'Настройки инбокса уведомлений',
            },
        ),
    ]
