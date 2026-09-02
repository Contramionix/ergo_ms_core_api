"""
Каталог platform-контрактов ModuleBridge и hook discovery.

Единый источник имён групп, событий и whitelist для линтера изоляции ядра.
Модули регистрируют провайдеры в integrations.py; ядро — в соответствующих integrations.
"""

from __future__ import annotations

# --- Groups (bridge.provide_many) ---

# Единый контракт session-claims: дескриптор
# {claim, request_attr?, entity_key?, resolve?, required_guard?}.
# Модуль декларирует свои session-claims одним дескриптором; ядро выводит из них
# список JWT claims, карту entity-resolver'ов и request-атрибуты.
SESSION_CLAIMS_GROUP = 'session_context.claims'

AUDIT_ACTION_DEFINITIONS_GROUP = 'audit.action_definitions'
# Измерения аудита (scope): по чему фильтровать/группировать журнал.
# Дескриптор: {key, label, resolve(request)->value|None, filter_param?, indexed?,
# read_guard?}. read_guard=True ограничивает чтение журнала не-админам.
AUDIT_SCOPE_DIMENSIONS_GROUP = 'audit.scope_dimensions'
NOTIFICATIONS_EVENT_DEFINITIONS_GROUP = 'notifications.event_definitions'
NOTIFICATIONS_EMAIL_CONTEXT_GROUP = 'notifications.email_context'
NOTIFICATIONS_EMAIL_TEMPLATES_GROUP = 'notifications.email_templates'

# Политики частоты загрузок media_api: модуль декларирует prefix + класс квоты.
# Ядро выбирает политику по target_dir; media_api считает по классу из токена.
MEDIA_UPLOAD_QUOTA_POLICIES_GROUP = 'media.upload_quota_policies'

# --- Ops (bridge.provide_op / call) ---

NOTIFICATIONS_CREATE = 'notifications.create'
NOTIFICATIONS_RECALL = 'notifications.recall'
# Динамические ops: f'{PREFIX}{module}'
NOTIFICATIONS_RENDER_EMAIL_PREFIX = 'notifications.render_email.'
NOTIFICATIONS_FILTER_EVENTS_PREFIX = 'notifications.filter_events_for_user.'

# --- Events (bridge.subscribe_to / emit / emit_first) ---

ADP_PERMISSION_CHECK = 'adp.permission_check'
# Процесс модуля (MODULE_AUTH_MODE=jwt_claims) не читает cms_adp_* у себя.
ADP_IS_ADMIN = 'adp.is_admin'
ADP_CHECK_API_ACCESS = 'adp.check_api_access'
ADP_CHECK_MODULE_PERMISSION = 'adp.check_module_permission'
# Подписчики возвращают iterable id RoleGroup, которые нельзя учитывать
# в глобальной агрегации ModulePermission (session-scoped системные группы и т.п.).
ADP_FILTER_GRANTED_ROLE_GROUP_IDS = 'adp.filter_granted_role_group_ids'
# Подписчики возвращают list ModulePermission (или совместимых объектов)
# для обогащения snapshot прав в контексте текущего session-scope.
ADP_SESSION_SCOPED_MODULE_PERMISSIONS = 'adp.session_scoped_module_permissions'
# Подписчики возвращают iterable пар (module_name, permission_key), которые
# нужно вычесть из snapshot прав текущего session-scope.
ADP_SESSION_SCOPED_DENIED_PERMISSIONS = 'adp.session_scoped_denied_permissions'
AUDIT_CAN_READ = 'audit.can_read'
AUDIT_RECORD = 'audit.record'

CORE_USER_DELETE = 'core.user_delete'
CORE_BULK_USER_CREATE = 'core.bulk_user_create'
# Необязательная подмена темы и тела служебных писем учётки.
# Нет провайдера, None или неполный dict — ядро шлёт свой текст.
# Ответ: {'subject': str, 'body': str, 'html_body'?: str}. Получателя и from
# модуль не задаёт. Kwargs JSON-примитивы.
CORE_COMPOSE_REGISTRATION_INVITATION = 'core.compose_registration_invitation'
CORE_COMPOSE_IMPORT_WELCOME_DEFAULTS = 'core.compose_import_welcome_defaults'
CORE_COMPOSE_PASSWORD_RESET_CODE = 'core.compose_password_reset_code'
CORE_COMPOSE_ADMIN_PASSWORD_RESET = 'core.compose_admin_password_reset'

# Провайдер возвращает dict session-claims для JWT при логине (или None).
SESSION_RESTORE_CLAIMS = 'session.restore_claims'
# Проверка: устройство активно и пользователь is_active (MODULE_AUTH_MODE=jwt_claims).
# Ответ: False или {'active', 'user_public_id', 'username', 'is_superuser', 'is_staff', 'is_admin'}.
# Старый bool True остаётся истинным; снимок нужен, если в JWT нет user_public_id.
# is_admin в JWT кладёт ядро (jwt_platform_claims); процесс модуля ему доверяет.
SESSION_DEVICE_ACTIVE = 'session.device_active'

MENU_PREPARE_VISIBILITY = 'menu.prepare_visibility'
MENU_CAN_SEE_ITEM = 'menu.can_see_item'

# --- Core bridge op prefixes (validate_module_isolation whitelist) ---

CORE_BRIDGE_PREFIXES = (
    'audit.',
    'notifications.',
    'core.',
    'session.',
    'menu.',
    'adp.',
    'media.',
)
