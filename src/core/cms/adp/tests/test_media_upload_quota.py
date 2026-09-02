"""Выбор класса квоты загрузки: longest prefix, infix, admin=max, allows."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from src.core.utils.media_upload_quota import (
    ResolvedUploadQuota,
    resolve_upload_quota,
)
from src.core.utils.media_views import MediaUploadTokenView


def _user(*, admin: bool = False) -> SimpleNamespace:
    return SimpleNamespace(is_authenticated=True, is_staff=admin, id=1)


def _policy(**overrides) -> dict:
    data = {
        'target_dir_prefix': 'lab/',
        'quota': 'lab_bulk',
        'rate': '100/minute',
        'allows': lambda user: True,
    }
    data.update(overrides)
    return data


class ResolveUploadQuotaTests(SimpleTestCase):
    def _resolve(self, policies: dict, *, user, target_dir: str, admin: bool = False):
        with (
            patch(
                'src.core.utils.media_upload_quota.bridge.all',
                return_value=policies,
            ),
            patch(
                'src.core.utils.media_upload_quota.PermissionService.is_admin',
                return_value=admin,
            ),
            patch(
                'src.core.utils.media_upload_quota.media_upload_rate_admin',
                return_value='120/minute',
            ),
            patch(
                'src.core.utils.media_upload_quota.default_upload_rate_ceiling',
                return_value='1000/minute',
            ),
        ):
            return resolve_upload_quota(user=user, target_dir=target_dir)

    def test_longest_prefix_wins(self):
        policies = {
            'wide': _policy(
                target_dir_prefix='lab/',
                quota='lab_wide',
                rate='10/minute',
            ),
            'nested': _policy(
                target_dir_prefix='lab/nested/',
                quota='lab_nested',
                rate='20/minute',
            ),
        }
        resolved = self._resolve(
            policies,
            user=_user(),
            target_dir='lab/nested/file.png',
        )
        self.assertEqual(resolved.quota, 'lab_nested')
        self.assertEqual(resolved.rate, '20/minute')

    def test_path_must_contain_wins_same_prefix(self):
        policies = {
            'course': _policy(
                target_dir_prefix='course/',
                quota='course_materials',
                rate='60/minute',
            ),
            'work': _policy(
                target_dir_prefix='course/',
                path_must_contain='/submissions/',
                quota='course_work',
                rate='20/minute',
            ),
        }
        work = self._resolve(
            policies,
            user=_user(),
            target_dir='course/org/1/submissions/a.pdf',
        )
        materials = self._resolve(
            policies,
            user=_user(),
            target_dir='course/org/1/lessons/a.pdf',
        )
        self.assertEqual(work.quota, 'course_work')
        self.assertEqual(work.rate, '20/minute')
        self.assertEqual(materials.quota, 'course_materials')
        self.assertEqual(materials.rate, '60/minute')

    def test_admin_gets_max_of_policy_and_admin_rate(self):
        policies = {
            'lab': _policy(rate='20/minute', quota='lab_bulk'),
        }
        resolved = self._resolve(
            policies,
            user=_user(admin=True),
            target_dir='lab/uploads/a.png',
            admin=True,
        )
        self.assertEqual(resolved.quota, 'lab_bulk')
        self.assertEqual(resolved.rate, '120/minute')

    def test_allows_false_falls_back_to_user(self):
        policies = {
            'lab': _policy(allows=lambda user: False),
        }
        resolved = self._resolve(
            policies,
            user=_user(),
            target_dir='lab/uploads/a.png',
        )
        self.assertEqual(resolved, ResolvedUploadQuota(quota='user'))
        self.assertIsNone(resolved.rate)

    def test_http_serialized_allows_module_keys(self):
        policies = {
            'lab': _policy(
                allows=None,
                allows_module='lab',
                allows_keys=['view'],
            ),
        }
        with patch(
            'src.core.utils.media_upload_quota.PermissionService.check_module_permission',
            return_value=True,
        ):
            resolved = self._resolve(
                policies,
                user=_user(),
                target_dir='lab/uploads/a.png',
            )
        self.assertEqual(resolved.quota, 'lab_bulk')
        self.assertEqual(resolved.rate, '100/minute')

    def test_unknown_prefix_falls_back_to_user(self):
        resolved = self._resolve(
            {'lab': _policy()},
            user=_user(),
            target_dir='other/dir/file.png',
        )
        self.assertEqual(resolved.quota, 'user')
        self.assertIsNone(resolved.rate)


class MediaUploadTokenIgnoresClientQuotaTests(SimpleTestCase):
    @patch('src.core.utils.media_views.get_upload_info')
    @patch('src.core.utils.media_views.resolve_upload_quota')
    def test_body_quota_is_ignored(self, mock_resolve, mock_info):
        mock_resolve.return_value = ResolvedUploadQuota(quota='user')
        mock_info.return_value = {'upload_url': 'http://x/upload/', 'token': 't'}
        request = MagicMock()
        request.user = SimpleNamespace(id=7)
        request.data = {
            'target_dir': 'avatars/',
            'quota': 'admin',
            'max_size': 10,
            'allowed_types': ['png'],
        }

        MediaUploadTokenView().post(request)

        mock_info.assert_called_once()
        kwargs = mock_info.call_args.kwargs
        self.assertEqual(kwargs['quota'], 'user')
        self.assertIsNone(kwargs['rate'])
        mock_resolve.assert_called_once_with(user=request.user, target_dir='avatars/')
