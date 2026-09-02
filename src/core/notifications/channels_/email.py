import logging

from django.db import transaction

logger = logging.getLogger('core.notifications')


class EmailChannel:
    """Канал доставки по электронной почте.

    Решение об отправке принимает PreferenceResolver (галочка «По эл. почте»
    в панели настроек). Сама отправка асинхронная — через Celery-задачу,
    поставленную строго после commit транзакции dispatch
    (см. transaction.on_commit), иначе воркер может не увидеть запись.

    Пауза: settings.NOTIFICATIONS_EMAIL_DELAY_SECONDS (env, default 300) —
    окно для notifications.recall без отправки письма.
    """

    name = 'email'

    def deliver(self, notification, *, created: bool) -> None:
        if not created:
            return  # повтор по idempotency_key — письмо уже ставилось

        from ..preferences import PreferenceResolver

        if not PreferenceResolver.is_enabled(
            notification.recipient_id,
            source_module=notification.source_module,
            event_key=notification.event_key,
            channel='email',
        ):
            return

        from src.core.utils.smtp_resolver import is_email_enabled

        if not is_email_enabled():
            logger.debug(
                'EmailChannel: пропуск notification=%s — EMAIL_ENABLED=false',
                notification.pk,
            )
            return

        notification_id = notification.pk

        def _enqueue():
            try:
                from django.conf import settings

                from ..tasks import send_notification_email_task

                delay_seconds = max(
                    0,
                    int(getattr(settings, 'NOTIFICATIONS_EMAIL_DELAY_SECONDS', 300) or 0),
                )
                send_notification_email_task.apply_async(
                    args=[notification_id],
                    countdown=delay_seconds,
                )
            except Exception:
                logger.exception(
                    'EmailChannel: не удалось поставить задачу отправки для notification=%s',
                    notification_id,
                )

        transaction.on_commit(_enqueue)
