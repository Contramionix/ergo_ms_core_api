"""HTTP-гейты при отключении функций в .env."""

from __future__ import annotations

from copy import deepcopy

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from src.core.cms.adp.consumers.presence import PresenceConsumer
from src.core.cms.adp.services.permissions import PermissionService

User = get_user_model()

_RF = deepcopy(settings.REST_FRAMEWORK)
_RF.setdefault('DEFAULT_THROTTLE_RATES', {})
_RF['DEFAULT_THROTTLE_RATES'] = {
    **_RF['DEFAULT_THROTTLE_RATES'],
    'anon': '10000/minute',
    'login': '10000/minute',
    'user': '10000/minute',
    'registration': '10000/minute',
    'password_reset': '10000/minute',
}


@override_settings(REST_FRAMEWORK=_RF)
class EnvFeatureGateTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    @override_settings(REGISTRATION_MODE='closed')
    def test_registration_closed_returns_403(self):
        response = self.client.post(
            reverse('registration'),
            {
                'username': 'newuser',
                'email': 'newuser@example.com',
                'password': 'TestPass123!',
                'first_name': 'New',
                'last_name': 'User',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(PASSWORD_RESET_ENABLED=False)
    def test_password_reset_disabled_returns_403(self):
        response = self.client.post(
            reverse('send_code'),
            {'email': 'anyone@example.com', 'purpose': 'password_reset'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(REALTIME_TRANSPORT='http_polling', REST_FRAMEWORK=_RF)
    def test_sse_stream_404_when_not_sse(self):
        PermissionService.ensure_system_roles()
        password = 'TestPass123!'
        User.objects.create_user(
            username='sse_user',
            email='sse@example.com',
            password=password,
        )
        login = self.client.post(
            reverse('authorization'),
            {'username': 'sse_user', 'password': password},
            format='json',
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {login.data["access"]}')
        response = self.client.get(reverse('realtime-stream'))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class WebsocketTransportGateTests(SimpleTestCase):
    @override_settings(REALTIME_TRANSPORT='http_polling')
    def test_ws_connect_rejected_when_not_websocket(self):
        async def _run():
            communicator = WebsocketCommunicator(
                PresenceConsumer.as_asgi(),
                '/ws/presence/',
            )
            connected, _ = await communicator.connect()
            self.assertFalse(connected)
            await communicator.disconnect()

        async_to_sync(_run)()
