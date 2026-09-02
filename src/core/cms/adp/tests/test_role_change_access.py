"""Доступ к эндпоинтам после смены роли: тот же JWT, ACL из БД."""

from __future__ import annotations

from copy import deepcopy

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from src.core.cms.adp.middleware.permission_request_cache import (
    clear_request_permission_cache,
)
from src.core.cms.adp.models import ModulePermission, Role, RoleGroup
from src.core.cms.adp.services.permissions import PermissionService

User = get_user_model()

_RF = deepcopy(settings.REST_FRAMEWORK)
_RF.setdefault('DEFAULT_THROTTLE_RATES', {})
_RF['DEFAULT_THROTTLE_RATES'] = {
    **_RF['DEFAULT_THROTTLE_RATES'],
    'anon': '10000/minute',
    'login': '10000/minute',
    'user': '10000/minute',
}

_LOCMEM_CACHE = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'adp-role-change-access-tests',
    },
}


def _login_access(client: APIClient, username: str, password: str) -> str:
    response = client.post(
        reverse('authorization'),
        {'username': username, 'password': password},
        format='json',
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    return response.data['access']


@override_settings(REST_FRAMEWORK=_RF)
class RoleChangeAccessTests(TestCase):
    password = 'TestPass123!'

    def setUp(self):
        cache.clear()
        PermissionService.ensure_system_roles()
        self.admin_role = PermissionService._get_or_create_admin_role()
        self.user_role = PermissionService._get_or_create_default_role()
        self.client = APIClient()

        self.keeper = User.objects.create_user(
            username='role_keeper',
            email='keeper@example.com',
            password=self.password,
        )
        PermissionService.assign_role_to_user(self.keeper, self.admin_role)

        self.victim = User.objects.create_user(
            username='role_victim',
            email='victim@example.com',
            password=self.password,
        )
        PermissionService.assign_role_to_user(self.victim, self.admin_role)

    def test_demotion_same_jwt_forbids_admin_keeps_session(self):
        token = _login_access(self.client, 'role_victim', self.password)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        before = self.client.get(reverse('policy_list'))
        self.assertEqual(before.status_code, status.HTTP_200_OK)

        PermissionService.assign_role_to_user(
            self.victim,
            self.user_role,
            assigned_by=self.keeper,
        )

        after_admin = self.client.get(reverse('policy_list'))
        self.assertEqual(after_admin.status_code, status.HTTP_403_FORBIDDEN)

        after_session = self.client.get(reverse('user_permissions'))
        self.assertEqual(after_session.status_code, status.HTTP_200_OK)
        self.assertFalse(after_session.data.get('is_global_admin'))

    def test_empty_role_groups_clears_module_grants(self):
        parent = Role.objects.create(name='access_audit_parent', is_system=False)
        group = RoleGroup.objects.create(name='access_audit_group', parent_role=parent)
        ModulePermission.objects.create(
            module_name='access_audit',
            permission_key='item_manage',
            permission_name='Manage',
            role_group=group,
            is_granted=True,
        )

        member = User.objects.create_user(
            username='role_member',
            email='member@example.com',
            password=self.password,
        )
        PermissionService.assign_role_to_user(
            member,
            self.user_role,
            role_groups=[group],
            assigned_by=self.keeper,
        )
        self.assertTrue(
            PermissionService.check_module_permission(
                member, 'access_audit', 'item_manage',
            )
        )

        PermissionService.assign_role_to_user(
            member,
            self.user_role,
            role_groups=[],
            assigned_by=self.keeper,
        )
        clear_request_permission_cache()
        member_role = PermissionService.get_user_role(member)
        self.assertEqual(list(member_role.role_groups.all()), [])
        self.assertFalse(
            PermissionService.check_module_permission(
                member, 'access_audit', 'item_manage',
            )
        )


@override_settings(
    REST_FRAMEWORK=_RF,
    CACHES=_LOCMEM_CACHE,
    PERMISSIONS_SNAPSHOT_CACHE_TTL=60,
)
class ModulePermissionSnapshotInvalidationTests(TestCase):
    password = 'TestPass123!'

    def setUp(self):
        cache.clear()
        PermissionService.ensure_system_roles()
        self.admin_role = PermissionService._get_or_create_admin_role()
        self.user_role = PermissionService._get_or_create_default_role()
        self.client = APIClient()

        self.admin = User.objects.create_user(
            username='snap_admin',
            email='snap_admin@example.com',
            password=self.password,
        )
        PermissionService.assign_role_to_user(self.admin, self.admin_role)

        parent = Role.objects.create(name='snap_parent', is_system=False)
        self.group = RoleGroup.objects.create(name='snap_group', parent_role=parent)
        self.perm = ModulePermission.objects.create(
            module_name='snap_mod',
            permission_key='item_manage',
            permission_name='Manage',
            role_group=self.group,
            is_granted=True,
        )

        self.member = User.objects.create_user(
            username='snap_member',
            email='snap_member@example.com',
            password=self.password,
        )
        PermissionService.assign_role_to_user(
            self.member,
            self.user_role,
            role_groups=[self.group],
            assigned_by=self.admin,
        )

    def test_module_permission_delete_refreshes_my_permissions(self):
        member_token = _login_access(self.client, 'snap_member', self.password)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {member_token}')
        before = self.client.get(reverse('user_permissions'))
        self.assertEqual(before.status_code, status.HTTP_200_OK)
        keys = {item['permission_key'] for item in before.data.get('module_permissions', [])}
        self.assertIn('item_manage', keys)

        admin_token = _login_access(APIClient(), 'snap_admin', self.password)
        admin_client = APIClient()
        admin_client.credentials(HTTP_AUTHORIZATION=f'Bearer {admin_token}')
        deleted = admin_client.delete(
            reverse('module_permission_detail', kwargs={'permission_id': self.perm.pk}),
        )
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)

        after = self.client.get(reverse('user_permissions'))
        self.assertEqual(after.status_code, status.HTTP_200_OK)
        keys_after = {
            item['permission_key'] for item in after.data.get('module_permissions', [])
        }
        self.assertNotIn('item_manage', keys_after)
