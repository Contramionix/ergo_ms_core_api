"""Заголовки transactional-писем (Message-ID, Auto-Submitted, List-Unsubscribe)."""

from __future__ import annotations

from django.core.mail import EmailMultiAlternatives
from django.test import SimpleTestCase, override_settings

from src.core.utils.transactional_email_headers import (
    apply_transactional_email_headers,
    message_id_domain,
)


class MessageIdDomainTests(SimpleTestCase):
    def test_from_email_domain(self):
        self.assertEqual(
            message_id_domain('ERGOMS <info@crm-ms-eco.ru>'),
            'crm-ms-eco.ru',
        )

    @override_settings(FRONTEND_BASE_URL='https://crm-ms-eco.ru')
    def test_fallback_frontend_host(self):
        self.assertEqual(message_id_domain(''), 'crm-ms-eco.ru')


class ApplyTransactionalHeadersTests(SimpleTestCase):
    def test_headers_and_reply_to(self):
        message = EmailMultiAlternatives(
            subject='Тема',
            body='Текст',
            from_email='ERGOMS <info@crm-ms-eco.ru>',
            to=['user@example.com'],
        )
        apply_transactional_email_headers(
            message,
            from_email='ERGOMS <info@crm-ms-eco.ru>',
            unsubscribe_url='https://crm-ms-eco.ru/user/notifications',
        )
        self.assertEqual(message.reply_to, ['info@crm-ms-eco.ru'])
        self.assertIn('Message-ID', message.extra_headers)
        self.assertTrue(str(message.extra_headers['Message-ID']).endswith('@crm-ms-eco.ru>'))
        self.assertEqual(message.extra_headers['Auto-Submitted'], 'auto-generated')
        self.assertEqual(
            message.extra_headers['List-Unsubscribe'],
            '<https://crm-ms-eco.ru/user/notifications>',
        )
        self.assertIn('Date', message.extra_headers)

    def test_without_unsubscribe(self):
        message = EmailMultiAlternatives(
            subject='Тема',
            body='Текст',
            from_email='info@crm-ms-eco.ru',
            to=['user@example.com'],
        )
        apply_transactional_email_headers(message, from_email='info@crm-ms-eco.ru')
        self.assertNotIn('List-Unsubscribe', message.extra_headers)
        self.assertEqual(message.extra_headers['Auto-Submitted'], 'auto-generated')
