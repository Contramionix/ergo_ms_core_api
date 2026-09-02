from django.urls import path

from src.core.cms.adp.dev_tools.views import (
    DevToolsPermissionCatalogView,
    DevToolsRolesView,
    DevToolsSessionView,
    DevToolsStatusView,
    DevToolsUsersView,
)

urlpatterns = [
    path('status/', DevToolsStatusView.as_view(), name='dev_tools_status'),
    path('session/', DevToolsSessionView.as_view(), name='dev_tools_session'),
    path('users/', DevToolsUsersView.as_view(), name='dev_tools_users'),
    path('roles/', DevToolsRolesView.as_view(), name='dev_tools_roles'),
    path(
        'permission-catalog/',
        DevToolsPermissionCatalogView.as_view(),
        name='dev_tools_permission_catalog',
    ),
]
