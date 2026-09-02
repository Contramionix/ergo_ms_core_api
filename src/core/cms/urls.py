from django.urls import path

from src.core.cms.views import (
    UserPublicInfoView,
    SyncAllProjectPages,
    GetCMSPages,
    SyncApiEndpointsView,
    GetApiEndpointsView,
)
from src.core.cms.disabled_modules_view import DisabledModulesView
from src.core.cms.client_browser_log import ClientBrowserLogView
from src.core.client_monitor.views import ClientMonitorIngestView

urlpatterns = [
    path('disabled-modules/', DisabledModulesView.as_view(), name='disabled-modules'),
    path('client-log/', ClientBrowserLogView.as_view(), name='client-browser-log'),
    path('client-monitor/events/', ClientMonitorIngestView.as_view(), name='cms-client-monitor-ingest'),


    path('users/by-ref/<uuid:ref>/public-info/', UserPublicInfoView.as_view(), name='user public info by ref'),

    path('patch-all-project-pages', SyncAllProjectPages.as_view(), name='set all pages'),
    path('get-cms-pages', GetCMSPages.as_view(), name='get all pages'),
    path('sync-api-endpoints', SyncApiEndpointsView.as_view(), name='sync_api_endpoints'),
    path('api-endpoints', GetApiEndpointsView.as_view(), name='get_api_endpoints'),
]
