"""Служебные письма учётки: текст ядра, если модуль договор не реализует."""

from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from src.core.cms.adp.services.import_users_welcome import (
    DEFAULT_WELCOME_BODY,
    DEFAULT_WELCOME_SUBJECT,
    get_welcome_email_defaults,
)
from src.core.cms.adp.services.password_reset_mail import (
    DEFAULT_ADMIN_RESET_SUBJECT,
    DEFAULT_RESET_CODE_SUBJECT,
    compose_admin_password_reset_email,
    compose_password_reset_code_email,
)
from src.core.integrations.module_contracts import (
    CORE_COMPOSE_ADMIN_PASSWORD_RESET,
    CORE_COMPOSE_IMPORT_WELCOME_DEFAULTS,
    CORE_COMPOSE_PASSWORD_RESET_CODE,
)

_BRIDGE = 'src.core.cms.adp.services.auth_mail_compose.bridge.call'


@override_settings(
    FRONTEND_BASE_URL='https://app.example.test',
    PASSWORD_RESET_CODE_TTL_MINUTES=15,
    PASSWORD_RESET_ENABLED=True,
)
class AuthMailComposeFallbackTests(SimpleTestCase):
    def test_password_reset_code_keeps_core_text(self):
        with patch(_BRIDGE, return_value=None) as mocked:
            composed = compose_password_reset_code_email(
                email='guest@example.test',
                code='123456',
            )
        self.assertEqual(mocked.call_args.args[0], CORE_COMPOSE_PASSWORD_RESET_CODE)
        self.assertEqual(composed.subject, DEFAULT_RESET_CODE_SUBJECT)
        self.assertEqual(composed.body, 'Ваш код подтверждения: 123456')
        self.assertEqual(mocked.call_args.kwargs['ttl_minutes'], 15)
        self.assertEqual(mocked.call_args.kwargs['login_url'], 'https://app.example.test/login')

    def test_password_reset_code_override(self):
        with patch(
            _BRIDGE,
            return_value={'subject': 'Код MS-CRM', 'body': 'Код: 123456'},
        ):
            composed = compose_password_reset_code_email(
                email='guest@example.test',
                code='123456',
            )
        self.assertEqual(composed.subject, 'Код MS-CRM')
        self.assertEqual(composed.body, 'Код: 123456')

    def test_admin_reset_keeps_core_text(self):
        with patch(_BRIDGE, return_value=None) as mocked:
            composed = compose_admin_password_reset_email(email='guest@example.test')
        self.assertEqual(mocked.call_args.args[0], CORE_COMPOSE_ADMIN_PASSWORD_RESET)
        self.assertEqual(composed.subject, DEFAULT_ADMIN_RESET_SUBJECT)
        self.assertIn('Администратор системы сбросил пароль', composed.body)
        self.assertIn('Забыл пароль', composed.body)

    @override_settings(PASSWORD_RESET_ENABLED=False)
    def test_admin_reset_hint_when_recovery_disabled(self):
        with patch(_BRIDGE, return_value=None) as mocked:
            composed = compose_admin_password_reset_email(email='guest@example.test')
        self.assertIn('обратитесь к администратору', composed.body)
        self.assertIn('администратору', mocked.call_args.kwargs['recovery_hint'])

    def test_welcome_defaults_keep_core_text(self):
        with patch(_BRIDGE, return_value=None) as mocked:
            defaults = get_welcome_email_defaults()
        self.assertEqual(mocked.call_args.args[0], CORE_COMPOSE_IMPORT_WELCOME_DEFAULTS)
        self.assertEqual(defaults['subject'], DEFAULT_WELCOME_SUBJECT)
        self.assertEqual(defaults['body'], DEFAULT_WELCOME_BODY)
        self.assertTrue(defaults['placeholders'])

    def test_welcome_defaults_override(self):
        with patch(
            _BRIDGE,
            return_value={'subject': 'Учётная запись', 'body': 'Логин: {username}'},
        ):
            defaults = get_welcome_email_defaults()
        self.assertEqual(defaults['subject'], 'Учётная запись')
        self.assertEqual(defaults['body'], 'Логин: {username}')
