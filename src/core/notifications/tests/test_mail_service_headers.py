"""MailService ставит transactional-заголовки и не шлёт pk в ссылке."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.core import mail
from django.test import SimpleTestCase, override_settings

from src.core.notifications.email_templates import RenderedEmail
from src.core.notifications.mail_service import MailService


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    FRONTEND_BASE_URL='https://crm-ms-eco.ru',
)
class MailServiceHeadersTests(SimpleTestCase):
    def test_sends_with_unsubscribe_and_auto_submitted(self):
        rendered = RenderedEmail(
            subject='Срок задачи через час',
            html_body='<p>ok</p>',
            text_body='ok',
            from_email=None,
        )
        notification = SimpleNamespace(pk=38, route={'name': 'CRMTasks'})
        connection = mail.get_connection()
        with (
            patch('src.core.notifications.mail_service.is_email_enabled', return_value=True),
            patch(
                'src.core.notifications.mail_service.resolve_connection_and_from',
                return_value=(connection, 'ERGOMS <info@crm-ms-eco.ru>'),
            ),
            patch(
                'src.core.notifications.mail_service.EmailTemplateResolver.resolve',
                return_value=rendered,
            ),
            patch('src.core.notifications.mail_service.apply_django_smtp_helo_override'),
        ):
            result = MailService.send_notification_email(
                notification=notification,
                recipient_email='user@example.com',
            )
        self.assertTrue(result.success)
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.extra_headers.get('Auto-Submitted'), 'auto-generated')
        self.assertEqual(
            sent.extra_headers.get('List-Unsubscribe'),
            '<https://crm-ms-eco.ru/user/notifications>',
        )
        self.assertIn('Message-ID', sent.extra_headers)
        self.assertNotIn('open=38', sent.body)
        self.assertNotIn('open=38', sent.alternatives[0][0] if sent.alternatives else '')
