"""
Celery-задачи доставки уведомлений.

Ядро не использует celery_config-механизм (CeleryModuleManager сканирует
только modules.*) — задачи объявляются через @shared_task и идут в очередь
по умолчанию, как принято в core (см. core/cms/adp/tasks.py).
"""

import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger('celery.core.notifications')

TRANSIENT_ERRORS = (ConnectionError, TimeoutError, OSError)


@shared_task(
    bind=True,
    name='core.notifications.send_email',
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=30,
    retry_kwargs={'max_retries': 3},
)
def send_notification_email_task(self, notification_id: int):
    from .mail_service import MailService
    from .models import Notification, NotificationEmailDelivery
    from .preferences import PreferenceResolver

    try:
        notification = Notification.objects.select_related('recipient').get(pk=notification_id)
    except Notification.DoesNotExist:
        logger.warning('send_email: уведомление %s не найдено', notification_id)
        return

    recipient = notification.recipient
    recipient_email = (getattr(recipient, 'email', '') or '').strip()

    delivery, _ = NotificationEmailDelivery.objects.get_or_create(
        notification=notification,
        defaults={'recipient_email': recipient_email},
    )
    if delivery.status == NotificationEmailDelivery.STATUS_SENT:
        return  # идемпотентность при Celery-retry/повторной постановке

    # Recall за паузу до отправки — письмо не уходит
    if notification.deleted_at is not None:
        delivery.status = NotificationEmailDelivery.STATUS_SKIPPED
        delivery.error_message = 'Отозвано (recall)'
        delivery.save(update_fields=['status', 'error_message'])
        logger.info(
            'send_email: пропуск notification=%s — отозвано (deleted_at)',
            notification_id,
        )
        return

    from src.core.utils.smtp_resolver import is_email_enabled

    if not is_email_enabled():
        delivery.status = NotificationEmailDelivery.STATUS_SKIPPED
        delivery.error_message = 'Email отключён глобально'
        delivery.save(update_fields=['status', 'error_message'])
        return

    # Настройки могли измениться между dispatch и выполнением задачи
    if not PreferenceResolver.is_enabled(
        recipient.pk,
        source_module=notification.source_module,
        event_key=notification.event_key,
        channel='email',
    ):
        delivery.status = NotificationEmailDelivery.STATUS_SKIPPED
        delivery.error_message = 'Отключено настройками пользователя'
        delivery.save(update_fields=['status', 'error_message'])
        return

    if not recipient_email:
        delivery.status = NotificationEmailDelivery.STATUS_SKIPPED
        delivery.error_message = 'У пользователя не указан email'
        delivery.save(update_fields=['status', 'error_message'])
        return

    result = MailService.send_notification_email(
        notification=notification,
        recipient_email=recipient_email,
    )

    if result.success:
        delivery.status = NotificationEmailDelivery.STATUS_SENT
        delivery.sent_at = timezone.now()
        delivery.error_message = ''
        delivery.save(update_fields=['status', 'sent_at', 'error_message'])
    else:
        delivery.status = NotificationEmailDelivery.STATUS_FAILED
        delivery.error_message = result.error[:2000]
        delivery.save(update_fields=['status', 'error_message'])


@shared_task(name='core.notifications.archive_stale_read')
def archive_stale_read_task():
    from .services import NotificationService

    count = NotificationService.archive_stale_read()
    if count:
        logger.info('archive_stale_read: в архив перенесено %s уведомлений', count)
    return count
