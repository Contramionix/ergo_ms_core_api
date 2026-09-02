"""Каталог действий аудита самого ядра (администрирование, аутентификация)."""

from .models import AuditEvent

MODULE = 'core.cms.adp'
MODULE_LABEL = 'Администрирование'

SETTINGS_MODULE = 'core.settings'
SETTINGS_MODULE_LABEL = 'Настройки системы'

CATEGORY_AUTH = 'auth'
CATEGORY_AUTH_LABEL = 'Аутентификация'
CATEGORY_USERS = 'users'
CATEGORY_USERS_LABEL = 'Пользователи и роли'
CATEGORY_ACCESS = 'access'
CATEGORY_ACCESS_LABEL = 'Права и политики'
CATEGORY_INVITES = 'invitations'
CATEGORY_INVITES_LABEL = 'Приглашения и регистрация'
CATEGORY_PROFILE = 'profile'
CATEGORY_PROFILE_LABEL = 'Заявки на изменение профиля'
CATEGORY_MENU = 'menu'
CATEGORY_MENU_LABEL = 'Меню'
CATEGORY_SETTINGS = 'settings'
CATEGORY_SETTINGS_LABEL = 'Настройки'
CATEGORY_UNDO = 'undo'
CATEGORY_UNDO_LABEL = 'Отмены'

# --- Аутентификация ---
AUTH_LOGIN = 'auth.login'
AUTH_LOGIN_FAILED = 'auth.login_failed'
AUTH_LOGOUT = 'auth.logout'

# --- Пользователи и роли ---
USER_ROLE_ASSIGNED = 'user.role_assigned'
USER_PASSWORD_CHANGED = 'user.password_changed'
USER_PASSWORD_RESET = 'user.password_reset'
USER_PASSWORD_RESET_ADMIN = 'user.password_reset_by_admin'
USER_CREATED = 'user.created'
USER_UPDATED = 'user.updated'
USER_DELETED = 'user.deleted'
USER_SUSPENDED = 'user.suspended'
USER_ACTIVATED = 'user.activated'
USER_SESSIONS_REVOKED = 'user.sessions_revoked'
USER_SESSION_REVOKED = 'user.session_revoked'

# --- Права и политики ---
ROLE_CREATED = 'role.created'
ROLE_UPDATED = 'role.updated'
ROLE_DELETED = 'role.deleted'
ROLE_GROUP_CREATED = 'role_group.created'
ROLE_GROUP_UPDATED = 'role_group.updated'
ROLE_GROUP_DELETED = 'role_group.deleted'
POLICY_CREATED = 'policy.created'
POLICY_UPDATED = 'policy.updated'
POLICY_DELETED = 'policy.deleted'
MODULE_PERMISSION_CREATED = 'module_permission.created'
MODULE_PERMISSION_UPDATED = 'module_permission.updated'
MODULE_PERMISSION_DELETED = 'module_permission.deleted'

# --- Приглашения / регистрация ---
INVITATION_CREATED = 'invitation.created'
INVITATION_REVOKED = 'invitation.revoked'
INVITATION_BULK_CREATED = 'invitation.bulk_created'
INVITATIONS_CLEARED = 'invitation.cleared'
REGISTRATION_SETTINGS_CHANGED = 'registration.settings_changed'

# --- Заявки на изменение профиля ---
PROFILE_CHANGE_APPROVED = 'profile_change.approved'
PROFILE_CHANGE_REJECTED = 'profile_change.rejected'

# --- Меню ---
MENU_ITEM_CREATED = 'menu.item_created'
MENU_ITEM_UPDATED = 'menu.item_updated'
MENU_ITEM_DELETED = 'menu.item_deleted'

# --- Отмены (toast Undo) ---
UNDO_PERFORMED = 'undo.performed'
THEME_UNDONE = 'theme.undone'  # legacy

# --- Настройки / темы ---
SETTINGS_CHANGED = 'settings.changed'
THEME_CREATED = 'theme.created'
THEME_UPDATED = 'theme.updated'
THEME_DELETED = 'theme.deleted'

_SEC = AuditEvent.SEVERITY_SECURITY
_INFO = AuditEvent.SEVERITY_INFO


def _a(action, label, *, icon='', category='', category_label='', severity='info'):
    return {
        'action': action,
        'label': label,
        'icon': icon,
        'category': category,
        'category_label': category_label,
        'severity': severity,
    }


def _auth(action, label, icon, severity=_SEC):
    return _a(action, label, icon=icon, category=CATEGORY_AUTH,
              category_label=CATEGORY_AUTH_LABEL, severity=severity)


def _users(action, label, icon, severity=_INFO):
    return _a(action, label, icon=icon, category=CATEGORY_USERS,
              category_label=CATEGORY_USERS_LABEL, severity=severity)


def _access(action, label, icon, severity=_SEC):
    return _a(action, label, icon=icon, category=CATEGORY_ACCESS,
              category_label=CATEGORY_ACCESS_LABEL, severity=severity)


def _invite(action, label, icon, severity=_INFO):
    return _a(action, label, icon=icon, category=CATEGORY_INVITES,
              category_label=CATEGORY_INVITES_LABEL, severity=severity)


def _profile(action, label, icon, severity=_INFO):
    return _a(action, label, icon=icon, category=CATEGORY_PROFILE,
              category_label=CATEGORY_PROFILE_LABEL, severity=severity)


def _menu(action, label, icon, severity=_INFO):
    return _a(action, label, icon=icon, category=CATEGORY_MENU,
              category_label=CATEGORY_MENU_LABEL, severity=severity)


def _undo(action, label, icon='Undo2', severity=_INFO):
    return _a(action, label, icon=icon, category=CATEGORY_UNDO,
              category_label=CATEGORY_UNDO_LABEL, severity=severity)


CORE_AUDIT_SECTION = {
    'module': MODULE,
    'module_label': MODULE_LABEL,
    'actions': [
        _auth(AUTH_LOGIN, 'Вход в систему', 'LogIn'),
        _auth(AUTH_LOGIN_FAILED, 'Неудачная попытка входа', 'ShieldAlert'),
        _auth(AUTH_LOGOUT, 'Выход из системы', 'LogOut', severity=_INFO),

        _users(USER_CREATED, 'Пользователь создан', 'UserPlus'),
        _users(USER_UPDATED, 'Пользователь изменён', 'UserCog'),
        _users(USER_DELETED, 'Пользователь удалён', 'UserX', severity=_SEC),
        _users(USER_SUSPENDED, 'Аккаунт приостановлен', 'UserRoundX', severity=_SEC),
        _users(USER_ACTIVATED, 'Аккаунт возобновлён', 'UserCheck', severity=_SEC),
        _users(USER_SESSIONS_REVOKED, 'Все сессии отозваны', 'ShieldOff', severity=_SEC),
        _users(USER_SESSION_REVOKED, 'Сессия отозвана', 'LogOut', severity=_SEC),
        _users(USER_ROLE_ASSIGNED, 'Назначена роль', 'KeySquare', severity=_SEC),
        _users(USER_PASSWORD_CHANGED, 'Смена пароля', 'KeyRound', severity=_SEC),
        _users(USER_PASSWORD_RESET, 'Восстановление пароля', 'KeyRound', severity=_SEC),
        _users(USER_PASSWORD_RESET_ADMIN, 'Сброс пароля администратором', 'KeyRound', severity=_SEC),

        _access(ROLE_CREATED, 'Роль создана', 'Shield'),
        _access(ROLE_UPDATED, 'Роль изменена', 'Shield'),
        _access(ROLE_DELETED, 'Роль удалена', 'Shield'),
        _access(ROLE_GROUP_CREATED, 'Ролевая группа создана', 'ShieldPlus'),
        _access(ROLE_GROUP_UPDATED, 'Ролевая группа изменена', 'ShieldPlus'),
        _access(ROLE_GROUP_DELETED, 'Ролевая группа удалена', 'ShieldMinus'),
        _access(POLICY_CREATED, 'Политика создана', 'FileLock2'),
        _access(POLICY_UPDATED, 'Политика изменена', 'FileLock2'),
        _access(POLICY_DELETED, 'Политика удалена', 'FileLock2'),
        _access(MODULE_PERMISSION_CREATED, 'Модульное право создано', 'Lock'),
        _access(MODULE_PERMISSION_UPDATED, 'Модульное право изменено', 'Lock'),
        _access(MODULE_PERMISSION_DELETED, 'Модульное право удалено', 'Unlock'),

        _invite(INVITATION_CREATED, 'Создано приглашение', 'MailPlus'),
        _invite(INVITATION_BULK_CREATED, 'Массовое создание приглашений', 'MailPlus'),
        _invite(INVITATION_REVOKED, 'Приглашение отозвано', 'MailX'),
        _invite(INVITATIONS_CLEARED, 'Очистка приглашений', 'Trash2'),
        _invite(REGISTRATION_SETTINGS_CHANGED, 'Изменены настройки регистрации', 'Settings', severity=_SEC),

        _profile(PROFILE_CHANGE_APPROVED, 'Заявка на профиль одобрена', 'CheckCircle2'),
        _profile(PROFILE_CHANGE_REJECTED, 'Заявка на профиль отклонена', 'XCircle'),

        _menu(MENU_ITEM_CREATED, 'Пункт меню создан', 'ListPlus'),
        _menu(MENU_ITEM_UPDATED, 'Пункт меню изменён', 'ListTree'),
        _menu(MENU_ITEM_DELETED, 'Пункт меню удалён', 'ListX'),

        _undo(UNDO_PERFORMED, 'Отменено действие'),
        _undo(THEME_UNDONE, 'Отменено действие с темой'),
    ],
}


CORE_SETTINGS_SECTION = {
    'module': SETTINGS_MODULE,
    'module_label': SETTINGS_MODULE_LABEL,
    'actions': [
        _a(SETTINGS_CHANGED, 'Настройки изменены', icon='Settings',
           category=CATEGORY_SETTINGS, category_label=CATEGORY_SETTINGS_LABEL, severity=_SEC),
        _a(THEME_CREATED, 'Тема создана', icon='Palette',
           category=CATEGORY_SETTINGS, category_label=CATEGORY_SETTINGS_LABEL),
        _a(THEME_UPDATED, 'Тема изменена', icon='Palette',
           category=CATEGORY_SETTINGS, category_label=CATEGORY_SETTINGS_LABEL),
        _a(THEME_DELETED, 'Тема удалена', icon='Palette',
           category=CATEGORY_SETTINGS, category_label=CATEGORY_SETTINGS_LABEL),
        _undo(UNDO_PERFORMED, 'Отменено действие'),
        _undo(THEME_UNDONE, 'Отменено действие с темой'),
    ],
}
