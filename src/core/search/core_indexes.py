"""Индексы поиска ядра ERGO MS."""

from __future__ import annotations

from django.contrib.auth import get_user_model

from src.core.audit.models import AuditEvent
from src.core.client_monitor.models import ClientMonitorSession
from src.core.cms.adp.models import (
  ModulePermission,
  RegistrationInvitation,
  Role,
  RoleGroup,
  UserProfileChangeRequest,
)

from .registry import SearchIndexDefinition, register_index

User = get_user_model()

# Meilisearch: uid — только [A-Za-z0-9_-], без точек.
INDEX_USERS = 'core_users'
INDEX_AUDIT = 'core_audit_events'
INDEX_INVITATIONS = 'core_invitations'
INDEX_PROFILE_CHANGE = 'core_profile_change_requests'
INDEX_CLIENT_MONITOR = 'core_client_monitor_sessions'
INDEX_ROLES = 'core_roles'
INDEX_ROLE_GROUPS = 'core_role_groups'
INDEX_MODULE_PERMISSIONS = 'core_module_permissions'


def _user_qs():
  return User.objects.all()


def _build_user_document(user) -> dict:
  return {
    'id': str(user.pk),
    'username': user.username or '',
    'email': user.email or '',
    'first_name': user.first_name or '',
    'last_name': user.last_name or '',
    'middle_name': getattr(user, 'middle_name', '') or '',
  }


def _audit_qs():
  return AuditEvent.objects.select_related('actor').all()


def _build_audit_document(event) -> dict:
  actor_username = ''
  if event.actor_id:
    actor_username = getattr(event.actor, 'username', '') or ''
  return {
    'id': str(event.pk),
    'actor_label': event.actor_label or '',
    'entity_label': event.entity_label or '',
    'actor_username': actor_username,
  }


def _invitations_qs():
  return RegistrationInvitation.objects.select_related('invited_by').all()


def _build_invitation_document(inv) -> dict:
  invited_by = ''
  if inv.invited_by_id:
    invited_by = getattr(inv.invited_by, 'username', '') or ''
  return {
    'id': str(inv.pk),
    'email': inv.email or '',
    'note': inv.note or '',
    'invited_by_username': invited_by,
  }


def _profile_change_qs():
  return UserProfileChangeRequest.objects.select_related('user').all()


def _build_profile_change_document(req) -> dict:
  user = req.user
  return {
    'id': str(req.pk),
    'email': req.email or '',
    'first_name': req.first_name or '',
    'last_name': req.last_name or '',
    'middle_name': req.middle_name or '',
    'phone': req.phone or '',
    'comment': req.comment or '',
    'user_username': getattr(user, 'username', '') if user else '',
    'user_email': getattr(user, 'email', '') if user else '',
  }


def _client_monitor_qs():
  return ClientMonitorSession.objects.all()


def _build_client_monitor_document(session) -> dict:
  return {
    'id': str(session.pk),
    'user_label': session.user_label or '',
    'user_public_id': session.user_public_id or '',
    'user_agent': session.user_agent or '',
    'client_version': session.client_version or '',
  }


def _roles_qs():
  return Role.objects.all()


def _build_role_document(role) -> dict:
  return {
    'id': str(role.pk),
    'name': role.name or '',
    'description': role.description or '',
    'role_type': role.role_type or '',
  }


def _role_groups_qs():
  return RoleGroup.objects.select_related('parent_role').all()


def _build_role_group_document(group) -> dict:
  parent_name = ''
  if group.parent_role_id:
    parent_name = getattr(group.parent_role, 'name', '') or ''
  return {
    'id': str(group.pk),
    'name': group.name or '',
    'description': group.description or '',
    'parent_role_name': parent_name,
  }


def _module_permissions_qs():
  return ModulePermission.objects.select_related('role_group').all()


def _build_module_permission_document(perm) -> dict:
  role_group_name = ''
  if perm.role_group_id:
    role_group_name = getattr(perm.role_group, 'name', '') or ''
  # Поля модели ModulePermission: permission_name / description (не name / resource_path).
  return {
    'id': str(perm.pk),
    'name': getattr(perm, 'permission_name', None) or getattr(perm, 'name', None) or '',
    'resource_path': getattr(perm, 'resource_path', None) or getattr(perm, 'description', None) or '',
    'module_name': perm.module_name or '',
    'permission_key': perm.permission_key or '',
    'role_group_name': role_group_name,
    'role_group_id': perm.role_group_id or 0,
  }


def register_core_indexes() -> None:
  register_index(SearchIndexDefinition(
    uid=INDEX_USERS,
    searchable_attributes=(
      'username', 'email', 'first_name', 'last_name', 'middle_name',
    ),
    build_document=_build_user_document,
    get_queryset=_user_qs,
  ))
  register_index(SearchIndexDefinition(
    uid=INDEX_AUDIT,
    searchable_attributes=('actor_label', 'entity_label', 'actor_username'),
    build_document=_build_audit_document,
    get_queryset=_audit_qs,
  ))
  register_index(SearchIndexDefinition(
    uid=INDEX_INVITATIONS,
    searchable_attributes=('email', 'note', 'invited_by_username'),
    build_document=_build_invitation_document,
    get_queryset=_invitations_qs,
  ))
  register_index(SearchIndexDefinition(
    uid=INDEX_PROFILE_CHANGE,
    searchable_attributes=(
      'email', 'first_name', 'last_name', 'middle_name', 'phone',
      'comment', 'user_username', 'user_email',
    ),
    build_document=_build_profile_change_document,
    get_queryset=_profile_change_qs,
  ))
  register_index(SearchIndexDefinition(
    uid=INDEX_CLIENT_MONITOR,
    searchable_attributes=(
      'user_label', 'user_public_id', 'user_agent', 'client_version',
    ),
    build_document=_build_client_monitor_document,
    get_queryset=_client_monitor_qs,
  ))
  register_index(SearchIndexDefinition(
    uid=INDEX_ROLES,
    searchable_attributes=('name', 'description', 'role_type'),
    build_document=_build_role_document,
    get_queryset=_roles_qs,
  ))
  register_index(SearchIndexDefinition(
    uid=INDEX_ROLE_GROUPS,
    searchable_attributes=('name', 'description', 'parent_role_name'),
    build_document=_build_role_group_document,
    get_queryset=_role_groups_qs,
  ))
  register_index(SearchIndexDefinition(
    uid=INDEX_MODULE_PERMISSIONS,
    searchable_attributes=(
      'name', 'resource_path', 'module_name', 'permission_key', 'role_group_name',
    ),
    filterable_attributes=('role_group_id',),
    build_document=_build_module_permission_document,
    get_queryset=_module_permissions_qs,
  ))
