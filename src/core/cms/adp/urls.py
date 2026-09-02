from django.urls import path, include

from src.core.cms.adp.token_refresh import DeviceBoundTokenRefreshView

from src.core.cms.adp.views_auth import (
    UserRegistrationValidationView,
    UserRegistrationView,
    UserAuthorizationView,
    PasswordResetSettingsView,
    SendConfirmationCodeView,
    VerifyConfirmationCodeView,
    ResetPasswordView,
)
from src.core.cms.adp.views import (
    LogoutView,
)
from src.core.cms.adp.views_bootstrap import SessionBootstrapView
from src.core.cms.adp.views_profile import (
    ChangePasswordView,
    UserDevicesView,
    UserDeviceDetailView,
    UserProfileView,
    UserSecuritySettingsView,
)
from src.core.cms.adp.views_import import (
    ImportUsersView,
    ImportUsersTaskStatusView,
    ImportUsersWelcomeEmailDefaultsView,
    ImportUsersPasswordsDownloadView,
)

from src.core.cms.adp.views_roles import (
    RoleListView,
    RoleDetailView,
    RoleGroupListView,
    RoleGroupDetailView,
    PolicyListView,
    PolicyDetailView,
    UserRoleAssignView,
    UserPermissionsView,
    CheckURLAccessView,
    ModulePermissionListView,
    ModulePermissionDetailView,
    ModuleCatalogView,
)
from src.core.cms.adp.views_admin_users import (
    AdminUserRoleListView,
    AdminUserDetailView,
    AdminUserAvatarView,
    AdminUserResetPasswordView,
    AdminUserStatusView,
    AdminUserDevicesView,
    AdminUserDeviceDetailView,
    AdminUserRevokeSessionsView,
)

from src.core.cms.adp.views_presence import (
    UserPresenceAdminSnapshotView,
    UserPresenceBatchView,
    UserPresenceHeartbeatView,
    UserPresenceOfflineView,
)
from src.core.cms.adp.views_profile_change import (
    ProfileSettingsView,
    UserProfileChangeRequestListCreateView,
    AdminProfileChangeRequestListView,
    AdminProfileChangeRequestApproveView,
    AdminProfileChangeRequestRejectView,
)
from src.core.cms.adp.views_invitations import (
    RegistrationSettingsView,
    ValidateInvitationView,
    RegistrationInvitationListView,
    RegistrationInvitationDetailView,
    RegistrationInvitationResendView,
    RegistrationInvitationBulkCreateView,
    RegistrationInvitationBulkSendView,
    RegistrationInvitationClearView,
)

urlpatterns = [
    # Authentication endpoints
    path('profile-settings/', ProfileSettingsView.as_view(), name='profile_settings'),
    path('profile-change-requests/', UserProfileChangeRequestListCreateView.as_view(), name='profile_change_requests'),
    path(
        'profile-change-requests/admin/',
        AdminProfileChangeRequestListView.as_view(),
        name='profile_change_requests_admin',
    ),
    path(
        'profile-change-requests/<int:request_id>/approve/',
        AdminProfileChangeRequestApproveView.as_view(),
        name='profile_change_request_approve',
    ),
    path(
        'profile-change-requests/<int:request_id>/reject/',
        AdminProfileChangeRequestRejectView.as_view(),
        name='profile_change_request_reject',
    ),
    path('registration-settings/', RegistrationSettingsView.as_view(), name='registration_settings'),
    path('password-reset-settings/', PasswordResetSettingsView.as_view(), name='password_reset_settings'),
    path('invitations/validate/', ValidateInvitationView.as_view(), name='validate_invitation'),
    path('invitations/', RegistrationInvitationListView.as_view(), name='registration_invitations'),
    path('invitations/bulk/', RegistrationInvitationBulkCreateView.as_view(), name='registration_invitations_bulk'),
    path('invitations/bulk/send/', RegistrationInvitationBulkSendView.as_view(), name='registration_invitations_bulk_send'),
    path('invitations/clear/', RegistrationInvitationClearView.as_view(), name='registration_invitations_clear'),
    path('invitations/<int:invitation_id>/', RegistrationInvitationDetailView.as_view(), name='registration_invitation_detail'),
    path(
        'invitations/<int:invitation_id>/resend/',
        RegistrationInvitationResendView.as_view(),
        name='registration_invitation_resend',
    ),
    path('validate-registration/', UserRegistrationValidationView.as_view(), name='validate_registration'),
    path('registration/', UserRegistrationView.as_view(), name='registration'),
    path('authorization/', UserAuthorizationView.as_view(), name='authorization'),

    path('send-code/', SendConfirmationCodeView.as_view(), name="send_code"),
    path('verify-code/', VerifyConfirmationCodeView.as_view(), name="verify_code"),
    path('reset-password/', ResetPasswordView.as_view(), name="reset_password"),

    path('token-refresh/', DeviceBoundTokenRefreshView.as_view(), name='token_refresh'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('session-bootstrap/', SessionBootstrapView.as_view(), name='session_bootstrap'),
    
    # Security endpoints
    path('change-password/', ChangePasswordView.as_view(), name='change_password'),
    path('devices/', UserDevicesView.as_view(), name='user_devices'),
    path('devices/<int:device_id>/', UserDeviceDetailView.as_view(), name='user_device_detail'),
    
    # Profile endpoints
    path('profile/', UserProfileView.as_view(), name='user_profile'),
    path('security-settings/', UserSecuritySettingsView.as_view(), name='user_security_settings'),
    
    # Role and Policy Management endpoints
    path('roles/', RoleListView.as_view(), name='role_list'),
    path('roles/<int:role_id>/', RoleDetailView.as_view(), name='role_detail'),
    path('role-groups/', RoleGroupListView.as_view(), name='role_group_list'),
    path('role-groups/<int:group_id>/', RoleGroupDetailView.as_view(), name='role_group_detail'),
    path('policies/', PolicyListView.as_view(), name='policy_list'),
    path('policies/<int:policy_id>/', PolicyDetailView.as_view(), name='policy_detail'),
    path('assign-role/', UserRoleAssignView.as_view(), name='assign_role'),
    path('my-permissions/', UserPermissionsView.as_view(), name='user_permissions'),
    path('check-url-access/', CheckURLAccessView.as_view(), name='check_url_access'),
    path('module-permissions/', ModulePermissionListView.as_view(), name='module_permissions'),
    path('module-permissions/<int:permission_id>/', ModulePermissionDetailView.as_view(), name='module_permission_detail'),
    path('module-catalog/', ModuleCatalogView.as_view(), name='module_catalog'),
    path('admin-users/', AdminUserRoleListView.as_view(), name='admin_users'),
    path('admin-users/by-ref/<uuid:ref>/', AdminUserDetailView.as_view(), name='admin_user_detail_by_ref'),
    path('admin-users/by-ref/<uuid:ref>/avatar/', AdminUserAvatarView.as_view(), name='admin_user_avatar_by_ref'),
    path(
        'admin-users/by-ref/<uuid:ref>/reset-password/',
        AdminUserResetPasswordView.as_view(),
        name='admin_user_reset_password_by_ref',
    ),
    path(
        'admin-users/by-ref/<uuid:ref>/status/',
        AdminUserStatusView.as_view(),
        name='admin_user_status_by_ref',
    ),
    path(
        'admin-users/by-ref/<uuid:ref>/devices/',
        AdminUserDevicesView.as_view(),
        name='admin_user_devices_by_ref',
    ),
    path(
        'admin-users/by-ref/<uuid:ref>/devices/<int:device_id>/',
        AdminUserDeviceDetailView.as_view(),
        name='admin_user_device_detail_by_ref',
    ),
    path(
        'admin-users/by-ref/<uuid:ref>/revoke-sessions/',
        AdminUserRevokeSessionsView.as_view(),
        name='admin_user_revoke_sessions_by_ref',
    ),
    path('presence/', UserPresenceBatchView.as_view(), name='user_presence_batch'),
    path('presence/heartbeat/', UserPresenceHeartbeatView.as_view(), name='user_presence_heartbeat'),
    path('presence/offline/', UserPresenceOfflineView.as_view(), name='user_presence_offline'),
    path('presence/admin-snapshot/', UserPresenceAdminSnapshotView.as_view(), name='user_presence_admin_snapshot'),
    path('import-users/', ImportUsersView.as_view(), name='import_users'),
    path(
        'import-users/welcome-email-defaults/',
        ImportUsersWelcomeEmailDefaultsView.as_view(),
        name='import_users_welcome_email_defaults',
    ),
    path('import-users/status/<str:task_id>/', ImportUsersTaskStatusView.as_view(), name='import_users_status'),
    path(
        'import-users/passwords/<str:task_id>/',
        ImportUsersPasswordsDownloadView.as_view(),
        name='import_users_passwords',
    ),
    
    path('dev-tools/', include('src.core.cms.adp.dev_tools.urls')),
    # Menu Management endpoints (подключаем подмодуль menu)
    path('menu/', include('src.core.cms.adp.menu.urls')),
]