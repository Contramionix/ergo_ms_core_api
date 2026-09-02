"""Письмо-приглашение: текст ядра, если модуль договор не реализует."""

from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from src.core.cms.adp.services.registration_invitation_mail import (
    ComposedInvitationEmail,
    DEFAULT_INVITATION_SUBJECT,
    compose_registration_invitation_email,
    send_registration_invitation_email,
)
from src.core.integrations.module_contracts import CORE_COMPOSE_REGISTRATION_INVITATION

_INVITE_TOKEN = 'invite-token-example'
_INVITE_URL = f'https://app.example.test/register?invite={_INVITE_TOKEN}'


@override_settings(FRONTEND_BASE_URL='https://app.example.test')
class ComposeRegistrationInvitationTests(SimpleTestCase):
    def _compose(self, *, invite_url=_INVITE_URL, ttl_days=7):
        return compose_registration_invitation_email(
            email='guest@example.test',
            invite_url=invite_url,
            ttl_days=ttl_days,
        )

    def _compose_without_provider(self, **kwargs):
        with patch(
            'src.core.cms.adp.services.auth_mail_compose.bridge.call',
            return_value=None,
        ):
            return self._compose(**kwargs)

    def test_without_provider_keeps_core_text(self):
        composed = self._compose_without_provider()
        self.assertEqual(composed.subject, DEFAULT_INVITATION_SUBJECT)
        self.assertIn('ERGOMS (app.example.test)', composed.body)
        self.assertIn('https://app.example.test/register', composed.body)
        self.assertIn(_INVITE_TOKEN, composed.body)
        self.assertNotIn('?invite=', composed.body)
        self.assertIsNone(composed.html_body)

    def test_without_provider_ttl_one_day_label(self):
        composed = self._compose_without_provider(ttl_days=1)
        self.assertIn('Код действует 1 день.', composed.body)

    def test_without_token_uses_register_page_copy(self):
        composed = self._compose_without_provider(
            invite_url='https://app.example.test/register',
        )
        self.assertIn('Откройте страницу регистрации: https://app.example.test/register', composed.body)
        self.assertIn('Ссылка действительна 7 дн.', composed.body)

    def test_module_override_replaces_subject_and_body(self):
        override = {
            'subject': 'Приглашение в контур',
            'body': 'Свой текст модуля',
            'html_body': '<p>Свой HTML</p>',
        }
        with patch(
            'src.core.cms.adp.services.auth_mail_compose.bridge.call',
            return_value=override,
        ) as mocked:
            composed = self._compose()

        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.args[0], CORE_COMPOSE_REGISTRATION_INVITATION)
        call_kwargs = mocked.call_args.kwargs
        self.assertIsNone(call_kwargs.get('default'))
        self.assertEqual(call_kwargs['email'], 'guest@example.test')
        self.assertEqual(call_kwargs['token'], _INVITE_TOKEN)
        self.assertEqual(call_kwargs['default_subject'], DEFAULT_INVITATION_SUBJECT)
        self.assertIn('ERGOMS', call_kwargs['default_body'])
        self.assertEqual(composed.subject, 'Приглашение в контур')
        self.assertEqual(composed.body, 'Свой текст модуля')
        self.assertEqual(composed.html_body, '<p>Свой HTML</p>')

    def test_none_from_provider_keeps_core_text(self):
        with patch(
            'src.core.cms.adp.services.auth_mail_compose.bridge.call',
            return_value=None,
        ):
            composed = self._compose()
        self.assertEqual(composed.subject, DEFAULT_INVITATION_SUBJECT)
        self.assertIn(_INVITE_TOKEN, composed.body)

    def test_incomplete_dict_keeps_core_text(self):
        with patch(
            'src.core.cms.adp.services.auth_mail_compose.bridge.call',
            return_value={'subject': 'Только тема'},
        ):
            composed = self._compose()
        self.assertEqual(composed.subject, DEFAULT_INVITATION_SUBJECT)

    def test_empty_body_keeps_core_text(self):
        with patch(
            'src.core.cms.adp.services.auth_mail_compose.bridge.call',
            return_value={'subject': 'Тема', 'body': '   '},
        ):
            composed = self._compose()
        self.assertEqual(composed.subject, DEFAULT_INVITATION_SUBJECT)

    def test_provider_exception_keeps_core_text(self):
        with patch(
            'src.core.cms.adp.services.auth_mail_compose.bridge.call',
            side_effect=RuntimeError('composer failed'),
        ):
            composed = self._compose()
        self.assertEqual(composed.subject, DEFAULT_INVITATION_SUBJECT)
        self.assertIn(_INVITE_TOKEN, composed.body)

    def test_send_uses_composed_fields_and_core_recipient(self):
        with (
            patch(
                'src.core.cms.adp.services.registration_invitation_mail.compose_registration_invitation_email',
                return_value=ComposedInvitationEmail(
                    subject='Тема модуля',
                    body='Тело модуля',
                    html_body='<p>HTML</p>',
                ),
            ),
            patch(
                'src.core.cms.adp.services.registration_invitation_mail.send_plain_email',
                return_value=(True, None),
            ) as mocked_send,
        ):
            ok, error = send_registration_invitation_email(
                'guest@example.test',
                _INVITE_URL,
                7,
            )

        self.assertTrue(ok)
        self.assertIsNone(error)
        mocked_send.assert_called_once_with(
            subject='Тема модуля',
            body='Тело модуля',
            recipients=['guest@example.test'],
            html_body='<p>HTML</p>',
        )
