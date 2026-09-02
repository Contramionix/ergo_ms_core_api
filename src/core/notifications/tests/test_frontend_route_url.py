"""Сборка URL писем уведомлений из Vue-route (без pk в query)."""

from __future__ import annotations

from types import SimpleNamespace

from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from src.core.notifications.email_templates import _resolve_action_url
from src.core.notifications.frontend_route_url import (
    build_frontend_url_from_route,
    fill_vue_path,
    inbox_url,
)

_PATH_INDEX = {
    'CRMTasks': '/crm/tasks/:taskId?',
    'CRMRequestDetail': '/crm/requests/:requestId',
    'CRMEquipmentDetail': '/crm/equipment/:equipmentId',
    'CRMIncomingRequests': '/crm/incoming-requests',
}

_TASK_ID = 'a9950227-d5c5-4c01-b9b8-f6d6ab81497d'
_REQUEST_ID = '8a5738e5-bb14-4d21-bb90-9463739435f9'
_BASE = 'https://crm-ms-eco.ru'


class FillVuePathTests(SimpleTestCase):
    def test_optional_param_present(self):
        self.assertEqual(
            fill_vue_path('/crm/tasks/:taskId?', {'taskId': _TASK_ID}),
            f'/crm/tasks/{_TASK_ID}',
        )

    def test_optional_param_absent(self):
        self.assertEqual(fill_vue_path('/crm/tasks/:taskId?', {}), '/crm/tasks')

    def test_required_param_missing(self):
        self.assertIsNone(fill_vue_path('/crm/requests/:requestId', {}))


class BuildFrontendUrlFromRouteTests(SimpleTestCase):
    def test_crm_tasks_with_uuid(self):
        url = build_frontend_url_from_route(
            {'name': 'CRMTasks', 'params': {'taskId': _TASK_ID}},
            base_url=_BASE,
            path_index=_PATH_INDEX,
        )
        self.assertEqual(url, f'{_BASE}/crm/tasks/{_TASK_ID}')

    def test_crm_request_detail(self):
        url = build_frontend_url_from_route(
            {'name': 'CRMRequestDetail', 'params': {'requestId': _REQUEST_ID}},
            base_url=_BASE,
            path_index=_PATH_INDEX,
        )
        self.assertEqual(url, f'{_BASE}/crm/requests/{_REQUEST_ID}')

    def test_crm_incoming_requests(self):
        url = build_frontend_url_from_route(
            {'name': 'CRMIncomingRequests'},
            base_url=_BASE,
            path_index=_PATH_INDEX,
        )
        self.assertEqual(url, f'{_BASE}/crm/incoming-requests')

    def test_unknown_route(self):
        self.assertIsNone(
            build_frontend_url_from_route(
                {'name': 'UnknownRoute'},
                base_url=_BASE,
                path_index=_PATH_INDEX,
            )
        )

    def test_live_crm_index_tasks_path(self):
        url = build_frontend_url_from_route(
            {'name': 'CRMTasks', 'params': {'taskId': _TASK_ID}},
            base_url=_BASE,
        )
        self.assertEqual(url, f'{_BASE}/crm/tasks/{_TASK_ID}')


@override_settings(FRONTEND_BASE_URL=_BASE)
class ResolveActionUrlTests(SimpleTestCase):
    def test_route_without_open_pk(self):
        notification = SimpleNamespace(
            pk=38,
            link_url='',
            route={'name': 'CRMTasks', 'params': {'taskId': _TASK_ID}},
        )
        with patch(
            'src.core.notifications.email_templates.build_frontend_url_from_route',
            return_value=f'{_BASE}/crm/tasks/{_TASK_ID}',
        ):
            url = _resolve_action_url(notification)
        self.assertEqual(url, f'{_BASE}/crm/tasks/{_TASK_ID}')
        self.assertNotIn('open=', url)
        self.assertNotIn('38', url)

    def test_unknown_route_falls_back_to_inbox(self):
        notification = SimpleNamespace(
            pk=38,
            link_url='',
            route={'name': 'UnknownRoute'},
        )
        with patch(
            'src.core.notifications.email_templates.build_frontend_url_from_route',
            return_value=None,
        ):
            url = _resolve_action_url(notification)
        self.assertEqual(url, inbox_url(_BASE))
        self.assertNotIn('open=', url)

    def test_relative_link_url(self):
        notification = SimpleNamespace(
            pk=38,
            link_url='/crm/tasks',
            route=None,
        )
        self.assertEqual(_resolve_action_url(notification), f'{_BASE}/crm/tasks')
