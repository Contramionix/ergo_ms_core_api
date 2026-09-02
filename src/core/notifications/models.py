from django.conf import settings
from django.db import models
from django.db.models import Q


class Notification(models.Model):
    LEVEL_INFO = 'info'
    LEVEL_SUCCESS = 'success'
    LEVEL_WARNING = 'warning'
    LEVEL_ERROR = 'error'
    LEVEL_CHOICES = (
        (LEVEL_INFO, 'Информация'),
        (LEVEL_SUCCESS, 'Успех'),
        (LEVEL_WARNING, 'Предупреждение'),
        (LEVEL_ERROR, 'Ошибка'),
    )

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='core_inbox_notifications',
        verbose_name='Получатель',
    )
    title = models.CharField(max_length=255, verbose_name='Заголовок')
    body = models.TextField(blank=True, default='', verbose_name='Текст')
    level = models.CharField(
        max_length=16,
        choices=LEVEL_CHOICES,
        default=LEVEL_INFO,
        verbose_name='Тип',
    )
    icon = models.CharField(
        max_length=64,
        blank=True,
        default='',
        verbose_name='Имя иконки',
    )
    source_module = models.CharField(
        max_length=64,
        blank=True,
        default='',
        verbose_name='Модуль-источник',
    )
    event_key = models.CharField(
        max_length=128,
        blank=True,
        default='',
        verbose_name='Ключ события',
    )
    link_url = models.CharField(
        max_length=512,
        blank=True,
        default='',
        verbose_name='URL для перехода',
    )
    route = models.JSONField(
        blank=True,
        null=True,
        verbose_name='Vue Router (name + params)',
    )
    meta = models.JSONField(
        blank=True,
        default=dict,
        verbose_name='Доп. данные',
    )
    actions = models.JSONField(
        blank=True,
        default=list,
        verbose_name='Кнопки действий',
        help_text='Список {id, label, style, handler} для интерактивных уведомлений',
    )
    actions_state = models.CharField(
        max_length=16,
        blank=True,
        null=True,
        verbose_name='Состояние действий',
        help_text='null — без действий; pending / resolved / expired',
    )
    resolved_action_id = models.CharField(
        max_length=64,
        blank=True,
        default='',
        verbose_name='Выбранное действие',
    )
    resolved_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Действие выполнено в',
    )
    idempotency_key = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name='Ключ идемпотентности',
    )
    is_read = models.BooleanField(default=False, verbose_name='Прочитано')
    in_app_visible = models.BooleanField(
        default=True,
        verbose_name='Показывать в клиенте',
        help_text='False — email-only уведомление: запись хранит данные для письма, но не отображается в inbox',
    )
    archived_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='В архиве с',
        help_text='Не показывается в колокольчике и основном списке',
    )
    deleted_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Отозвано в',
        help_text='Отозвано системой / модулем — скрыто из UI (не удаление пользователем)',
    )
    sidebar_hidden_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Скрыто из колокольчика',
        help_text='Не показывается в dropdown, остаётся в истории',
    )
    inbox_restored_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Восстановлено в inbox',
        help_text='После «Из архива»: отсчёт автоархивации с этой даты, иначе с created_at',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Создано')
    read_at = models.DateTimeField(blank=True, null=True, verbose_name='Прочитано в')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Уведомление'
        verbose_name_plural = 'Уведомления'
        indexes = [
            models.Index(
                fields=['recipient', 'is_read', 'created_at'],
                name='core_notif_recipient_idx',
            ),
            models.Index(
                fields=['recipient', 'deleted_at', 'archived_at', 'created_at'],
                name='core_notif_inbox_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['recipient', 'idempotency_key'],
                name='uniq_recipient_idem',
                condition=Q(idempotency_key__isnull=False),
            ),
        ]

    def __str__(self):
        return f'[{self.level}] {self.title} -> user_id={self.recipient_id}'


class NotificationPreference(models.Model):
    """Per-event/per-channel предпочтение пользователя.

    Sentinel-строка (source_module='*', event_key='*') — глобальный
    master-switch канала. Отсутствие записи означает default из каталога
    событий (bridge.all('notifications.event_definitions')).
    """

    CHANNEL_IN_APP = 'in_app'
    CHANNEL_EMAIL = 'email'
    CHANNEL_CHOICES = (
        (CHANNEL_IN_APP, 'В клиенте'),
        (CHANNEL_EMAIL, 'По эл. почте'),
    )

    GLOBAL_KEY = '*'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='core_notification_preferences',
        verbose_name='Пользователь',
    )
    source_module = models.CharField(
        max_length=64,
        blank=True,
        default='',
        verbose_name='Модуль-источник',
    )
    event_key = models.CharField(max_length=128, verbose_name='Ключ события')
    channel = models.CharField(
        max_length=16,
        choices=CHANNEL_CHOICES,
        verbose_name='Канал',
    )
    enabled = models.BooleanField(verbose_name='Включено')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлено')

    class Meta:
        verbose_name = 'Настройка уведомлений'
        verbose_name_plural = 'Настройки уведомлений'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'source_module', 'event_key', 'channel'],
                name='uniq_user_event_channel_pref',
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'channel'], name='core_notif_pref_user_idx'),
        ]

    def __str__(self):
        state = 'on' if self.enabled else 'off'
        return f'{self.user_id}:{self.source_module}.{self.event_key}[{self.channel}]={state}'


class NotificationUserSettings(models.Model):
    """Персональные настройки инбокса (не каналы доставки)."""

    SIDEBAR_ACTIVITY_DAYS_MIN = 1
    SIDEBAR_ACTIVITY_DAYS_MAX = 7
    SIDEBAR_ACTIVITY_DAYS_DEFAULT = 3

    AUTO_ARCHIVE_DAYS_PRESETS = (7, 14, 30, 60, 90)
    AUTO_ARCHIVE_DAYS_DEFAULT = 14

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='core_notification_user_settings',
        verbose_name='Пользователь',
    )
    sidebar_activity_days = models.PositiveSmallIntegerField(
        default=SIDEBAR_ACTIVITY_DAYS_DEFAULT,
        verbose_name='Активность в колокольчике (дней)',
        help_text='Сколько дней держать прочитанные уведомления в колокольчике (1–7)',
    )
    auto_archive_days = models.PositiveSmallIntegerField(
        default=AUTO_ARCHIVE_DAYS_DEFAULT,
        verbose_name='Автоархив (дней)',
        help_text='Через сколько дней прочитанные уходят в архив (7, 14, 30, 60, 90)',
    )

    class Meta:
        verbose_name = 'Настройки инбокса уведомлений'
        verbose_name_plural = 'Настройки инбокса уведомлений'

    def __str__(self):
        return (
            f'user={self.user_id} sidebar_days={self.sidebar_activity_days} '
            f'archive_days={self.auto_archive_days}'
        )


class NotificationEmailDelivery(models.Model):
    """Журнал email-доставки уведомления.

    OneToOne к Notification гарантирует идемпотентность: одно уведомление —
    максимум одно письмо (получатель у уведомления один).
    """

    STATUS_PENDING = 'pending'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    STATUS_SKIPPED = 'skipped'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'Ожидает'),
        (STATUS_SENT, 'Отправлено'),
        (STATUS_FAILED, 'Ошибка'),
        (STATUS_SKIPPED, 'Пропущено'),
    )

    notification = models.OneToOneField(
        Notification,
        on_delete=models.CASCADE,
        related_name='email_delivery',
        verbose_name='Уведомление',
    )
    recipient_email = models.EmailField(verbose_name='Email получателя')
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        verbose_name='Статус',
    )
    sent_at = models.DateTimeField(blank=True, null=True, verbose_name='Отправлено в')
    error_message = models.TextField(blank=True, default='', verbose_name='Ошибка')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создано')

    class Meta:
        verbose_name = 'Email-доставка уведомления'
        verbose_name_plural = 'Email-доставки уведомлений'

    def __str__(self):
        return f'email->{self.recipient_email} [{self.status}] notif={self.notification_id}'
