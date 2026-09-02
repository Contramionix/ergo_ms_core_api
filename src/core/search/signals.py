"""Сигналы для инкрементальной индексации."""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from src.core.audit.models import AuditEvent
from src.core.client_monitor.models import ClientMonitorSession
from src.core.cms.adp.models import (
  ModulePermission,
  RegistrationInvitation,
  Role,
  RoleGroup,
  UserProfileChangeRequest,
)
from src.core.search.client import is_search_enabled
from src.core.search.core_indexes import (
  INDEX_AUDIT,
  INDEX_CLIENT_MONITOR,
  INDEX_INVITATIONS,
  INDEX_MODULE_PERMISSIONS,
  INDEX_PROFILE_CHANGE,
  INDEX_ROLE_GROUPS,
  INDEX_ROLES,
  INDEX_USERS,
)
from src.core.search.registry import get_index
from src.core.search.sync import delete_documents, ensure_registry_loaded, index_documents
from src.core.search.tasks import delete_document_task, index_document_task

logger = logging.getLogger('search')

User = get_user_model()

_MODEL_INDEX = {
  User: (INDEX_USERS, '_build_user_document', 'core_indexes'),
  AuditEvent: (INDEX_AUDIT, '_build_audit_document', 'core_indexes'),
  RegistrationInvitation: (INDEX_INVITATIONS, '_build_invitation_document', 'core_indexes'),
  UserProfileChangeRequest: (INDEX_PROFILE_CHANGE, '_build_profile_change_document', 'core_indexes'),
  ClientMonitorSession: (INDEX_CLIENT_MONITOR, '_build_client_monitor_document', 'core_indexes'),
  Role: (INDEX_ROLES, '_build_role_document', 'core_indexes'),
  RoleGroup: (INDEX_ROLE_GROUPS, '_build_role_group_document', 'core_indexes'),
  ModulePermission: (INDEX_MODULE_PERMISSIONS, '_build_module_permission_document', 'core_indexes'),
}


def _enqueue_index(index_uid: str, document: dict) -> None:
  """Ставит задачу в Celery; при недоступном брокере — синхронно (без падения команды)."""
  try:
    index_document_task.delay(index_uid, document)
    return
  except Exception:
    logger.warning(
      'search: не удалось поставить index_document в Celery (%s) — синхронная запись',
      index_uid,
      exc_info=True,
    )
  try:
    ensure_registry_loaded()
    defn = get_index(index_uid)
    if defn:
      index_documents(defn, [document])
  except Exception:
    logger.exception('search: синхронная индексация %s не удалась', index_uid)


def _enqueue_delete(index_uid: str, document_id: str) -> None:
  try:
    delete_document_task.delay(index_uid, document_id)
    return
  except Exception:
    logger.warning(
      'search: не удалось поставить delete_document в Celery (%s) — синхронное удаление',
      index_uid,
      exc_info=True,
    )
  try:
    delete_documents(index_uid, [document_id])
  except Exception:
    logger.exception('search: синхронное удаление из %s не удалось', index_uid)


def _schedule_index(index_uid: str, instance) -> None:
  if not is_search_enabled():
    return
  ensure_registry_loaded()
  defn = get_index(index_uid)
  if not defn or not defn.build_document:
    return
  document = defn.build_document(instance)

  def _run():
    _enqueue_index(index_uid, document)

  if transaction.get_connection().in_atomic_block:
    transaction.on_commit(_run)
  else:
    _run()


def _schedule_delete(index_uid: str, pk) -> None:
  if not is_search_enabled():
    return
  document_id = str(pk)

  def _run():
    _enqueue_delete(index_uid, document_id)

  if transaction.get_connection().in_atomic_block:
    transaction.on_commit(_run)
  else:
    _run()


def _connect_model(model, index_uid: str):
  @receiver(post_save, sender=model)
  def _on_save(sender, instance, **kwargs):
    _schedule_index(index_uid, instance)

  @receiver(post_delete, sender=model)
  def _on_delete(sender, instance, **kwargs):
    _schedule_delete(index_uid, instance.pk)


for _model, (_uid, _builder, _mod) in _MODEL_INDEX.items():
  _connect_model(_model, _uid)
