"""
URL маршруты для управления меню.
"""

from django.urls import path

from .views import (
    UserMenuView,
    MenuItemListView,
    MenuItemDetailView,
    MenuItemReorderView,
    MenuSeparatorListView,
    MenuSeparatorDetailView,
    MenuAccessLogView,
    AvailableIconsView,
    MenuRestoreView,
    MenuRestoreUndoView,
)

urlpatterns = [
    path('', UserMenuView.as_view(), name='user_menu'),
    path('items/', MenuItemListView.as_view(), name='menu_items'),
    path('items/<uuid:item_ref>/', MenuItemDetailView.as_view(), name='menu_item_detail'),
    path('items/reorder/', MenuItemReorderView.as_view(), name='menu_items_reorder'),
    path('separators/', MenuSeparatorListView.as_view(), name='menu_separators'),
    path('separators/<uuid:separator_ref>/', MenuSeparatorDetailView.as_view(), name='menu_separator_detail'),
    path('access-log/', MenuAccessLogView.as_view(), name='menu_access_log'),
    path('available-icons/', AvailableIconsView.as_view(), name='available_icons'),
    path('restore/undo/', MenuRestoreUndoView.as_view(), name='menu_restore_undo'),
    path('restore/', MenuRestoreView.as_view(), name='menu_restore'),
]