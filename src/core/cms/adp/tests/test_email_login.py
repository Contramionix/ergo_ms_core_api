"""Тесты входа по username или email."""

from __future__ import annotations

from copy import deepcopy

from django.contrib.auth import authenticate, get_user_model
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()

_RF = deepcopy(settings.REST_FRAMEWORK)
_RF.setdefault('DEFAULT_THROTTLE_RATES', {})
_RF['DEFAULT_THROTTLE_RATES'] = {
    **_RF['DEFAULT_THROTTLE_RATES'],
    'anon': '10000/minute',
    'login': '10000/minute',
}


@override_settings(REST_FRAMEWORK=_RF)
class EmailOrUsernameBackendTests(TestCase):
    def setUp(self):
        self.password = 'TestPass123!'
        self.user = User.objects.create_user(
            username='alice',
            email='alice@example.com',
            password=self.password,
        )

    def test_authenticate_by_username(self):
        user = authenticate(username='alice', password=self.password)
        self.assertEqual(user, self.user)

    def test_authenticate_by_email(self):
        user = authenticate(username='alice@example.com', password=self.password)
        self.assertEqual(user, self.user)

    def test_authenticate_by_email_case_insensitive(self):
        user = authenticate(username='Alice@Example.COM', password=self.password)
        self.assertEqual(user, self.user)

    def test_wrong_password(self):
        self.assertIsNone(
            authenticate(username='alice@example.com', password='wrong'),
        )

    def test_unknown_email(self):
        self.assertIsNone(
            authenticate(username='nobody@example.com', password=self.password),
        )

    def test_duplicate_email_fail_closed(self):
        User.objects.create_user(
            username='alice2',
            email='alice@example.com',
            password=self.password,
        )
        self.assertIsNone(
            authenticate(username='alice@example.com', password=self.password),
        )

    def test_inactive_user(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertIsNone(
            authenticate(username='alice@example.com', password=self.password),
        )


@override_settings(REST_FRAMEWORK=_RF)
class AuthorizationApiEmailLoginTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.login_url = reverse('authorization')
        self.password = 'TestPass123!'
        self.user = User.objects.create_user(
            username='bob',
            email='bob@example.com',
            password=self.password,
        )

    def test_login_by_username(self):
        response = self.client.post(
            self.login_url,
            {'username': 'bob', 'password': self.password},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)

    def test_login_by_email(self):
        response = self.client.post(
            self.login_url,
            {'username': 'bob@example.com', 'password': self.password},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)

    def test_login_suspended_by_email(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        response = self.client.post(
            self.login_url,
            {'username': 'bob@example.com', 'password': self.password},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_login_invalid_credentials(self):
        response = self.client.post(
            self.login_url,
            {'username': 'bob@example.com', 'password': 'wrong'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
