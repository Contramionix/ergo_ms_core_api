"""Безопасное удаление пользователя администратором."""

import logging

from django.apps import apps
from django.contrib.auth import get_user_model

User = get_user_model()
from django.db import connection, models, transaction
from django.db.models.deletion import ProtectedError
from django.db.utils import IntegrityError, ProgrammingError

from src.core.cms.adp.models import UserDevice, UserPresence, UserProfile, UserRole
from src.core.cms.adp.services import presence as presence_service
from src.core.integrations import bridge
from src.core.integrations.module_contracts import CORE_USER_DELETE
from src.core.settings.models import UserAvatar

logger = logging.getLogger(__name__)

_MAX_FK_CLEANUP_PASSES = 10


class UserDeletionBlockedError(Exception):
    """Удаление заблокировано связанными данными в БД."""

    def __init__(self, message: str = '', *, detail: str = ''):
        super().__init__(message or detail)
        self.detail = detail


def delete_admin_user(user: User) -> None:
    """
    Удаляет пользователя: отзывает сессии, вызывает bridge-хуки, очищает связи ядра.

    Raises:
        UserDeletionBlockedError: если удаление заблокировано PROTECT/FK в БД.
    """
    logger.info(
        'Starting admin user deletion: user_id=%s username=%s',
        user.pk,
        user.username,
    )
    revoke_user_auth(user)

    public_id = getattr(user, 'public_id', None)
    bridge.emit(
        CORE_USER_DELETE,
        user_id=user.pk,
        user_public_id=str(public_id) if public_id else '',
        username=user.username or '',
    )

    _cleanup_core_user_relations(user)
    _delete_user_record(user)
    logger.info(
        'Admin user deleted successfully: user_id=%s username=%s',
        user.pk,
        user.username,
    )


def revoke_user_auth(user: User, *, except_device_id: int | None = None) -> None:
    """
    Отзывает сессии и refresh-токены пользователя.

    except_device_id: не деактивировать это устройство и не blacklist его jti.
    """
    from src.core.cms.adp.services.session_devices import invalidate_device_session_cache

    devices_qs = UserDevice.objects.filter(user=user)
    except_jti = None
    if except_device_id is not None:
        except_device = devices_qs.filter(pk=except_device_id).only('outstanding_token_jti').first()
        if except_device is not None:
            except_jti = except_device.outstanding_token_jti or None
        devices_qs = devices_qs.exclude(pk=except_device_id)

    devices_qs.update(is_active=False)
    invalidate_device_session_cache(user.id)
    presence_service.reset_user(user.id)

    try:
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

        outstanding_qs = OutstandingToken.objects.filter(user=user)
        if except_jti:
            outstanding_qs = outstanding_qs.exclude(jti=except_jti)
        for outstanding in outstanding_qs:
            BlacklistedToken.objects.get_or_create(token=outstanding)
    except Exception:
        pass


def _cleanup_core_user_relations(user: User) -> None:
    UserRole.objects.filter(user=user).delete()
    UserRole.objects.filter(assigned_by=user).update(assigned_by=None)
    UserProfile.objects.filter(user=user).delete()
    UserDevice.objects.filter(user=user).delete()
    UserPresence.objects.filter(user_id=user.id).delete()
    UserAvatar.objects.filter(user=user).delete()

    try:
        from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

        OutstandingToken.objects.filter(user=user).delete()
    except Exception:
        pass


def _delete_user_record(user: User) -> None:
    user_id = user.id
    username = user.username

    try:
        user.delete()
        return
    except ProtectedError as exc:
        detail = _format_protected_objects(exc)
        _log_deletion_blocked(
            user_id,
            username,
            reason='protected_objects',
            detail=detail,
            exc=exc,
        )
        raise UserDeletionBlockedError(detail=detail) from exc
    except ProgrammingError as exc:
        logger.warning(
            'ORM user.delete() failed with ProgrammingError for user_id=%s username=%s, '
            'attempting SQL fallback: %s',
            user_id,
            username,
            exc,
        )

    try:
        with transaction.atomic():
            deleted_total = _delete_existing_user_fk_rows(user_id)
            logger.info(
                'SQL FK cleanup before user delete: user_id=%s username=%s rows_deleted=%s',
                user_id,
                username,
                deleted_total,
            )
            _delete_user_sql(user_id)
    except IntegrityError as exc:
        detail = _safe_format_remaining_fk_refs(user_id, exc)
        _log_deletion_blocked(
            user_id,
            username,
            reason='integrity_error',
            detail=detail,
            exc=exc,
        )
        raise UserDeletionBlockedError(detail=detail) from exc


def _format_protected_objects(exc: ProtectedError) -> str:
    protected = getattr(exc, 'protected_objects', None) or set()
    if not protected:
        return str(exc)

    grouped: dict[str, int] = {}
    for obj in protected:
        label = f'{obj._meta.app_label}.{obj._meta.model_name}'
        grouped[label] = grouped.get(label, 0) + 1

    parts = [f'{label} ({count})' for label, count in sorted(grouped.items())]
    return '; '.join(parts)


def _safe_format_remaining_fk_refs(user_id: int, exc: Exception) -> str:
    try:
        return _format_remaining_fk_refs(user_id)
    except Exception as detail_exc:
        logger.warning(
            'Failed to collect remaining FK refs for user_id=%s: %s',
            user_id,
            detail_exc,
            exc_info=True,
        )
        return str(exc)


def _format_remaining_fk_refs(user_id: int) -> str:
    remaining = _find_remaining_user_fk_refs(user_id)
    if not remaining:
        return 'remaining FK refs not detected'

    parts = [f'{table}.{column} ({count})' for table, column, count in remaining]
    return '; '.join(parts)


def _log_deletion_blocked(
    user_id: int,
    username: str,
    *,
    reason: str,
    detail: str,
    exc: Exception,
) -> None:
    logger.error(
        'Admin user deletion blocked: user_id=%s username=%s reason=%s detail=%s error=%s',
        user_id,
        username,
        reason,
        detail,
        exc,
        exc_info=True,
    )


def _collect_user_fk_refs():
    refs = []
    for model in apps.get_models():
        if model._meta.proxy or model is User:
            continue
        table = model._meta.db_table
        for field in model._meta.local_fields:
            if isinstance(field, models.ForeignKey) and field.remote_field.model is User:
                refs.append((table, field.column))
    return refs


def _find_remaining_user_fk_refs(user_id: int) -> list[tuple[str, str, int]]:
    existing_tables = set(connection.introspection.table_names())
    remaining: list[tuple[str, str, int]] = []

    for table, column in _collect_user_fk_refs():
        if table not in existing_tables:
            continue
        try:
            count = _count_rows_sql(table, column, user_id)
        except ProgrammingError as exc:
            logger.debug(
                'Skip remaining FK count for %s.%s (user_id=%s): %s',
                table,
                column,
                user_id,
                exc,
            )
            continue
        if count:
            remaining.append((table, column, count))

    remaining.sort(key=lambda item: (-item[2], item[0], item[1]))
    return remaining


def _count_rows_sql(table: str, column: str, user_id: int) -> int:
    quoted_table = connection.ops.quote_name(table)
    quoted_column = connection.ops.quote_name(column)
    with connection.cursor() as cursor:
        cursor.execute(
            f'SELECT COUNT(*) FROM {quoted_table} WHERE {quoted_column} = %s',
            [user_id],
        )
        return int(cursor.fetchone()[0])


def _delete_existing_user_fk_rows(user_id: int) -> int:
    """
    Удаляет строки в существующих таблицах БД с FK на auth_user.

    Обходит только таблицы, реально присутствующие в БД (модули без миграций пропускаются).
    Несколько проходов нужны для цепочек зависимостей между дочерними таблицами.

    Returns:
        Общее число удалённых строк.
    """
    existing_tables = set(connection.introspection.table_names())
    refs = _collect_user_fk_refs()
    deleted_total = 0

    for pass_index in range(_MAX_FK_CLEANUP_PASSES):
        deleted_any = False
        for table, column in refs:
            if table not in existing_tables:
                continue
            try:
                with transaction.atomic():
                    rowcount = _delete_rows_sql(table, column, user_id)
            except ProgrammingError as exc:
                logger.debug(
                    'Skip FK cleanup for %s.%s (user_id=%s): %s',
                    table,
                    column,
                    user_id,
                    exc,
                )
                continue
            except IntegrityError as exc:
                logger.warning(
                    'FK cleanup blocked for %s.%s (user_id=%s): %s',
                    table,
                    column,
                    user_id,
                    exc,
                )
                continue
            if rowcount:
                deleted_any = True
                deleted_total += rowcount
                logger.debug(
                    'FK cleanup deleted %s row(s) from %s.%s (user_id=%s, pass=%s)',
                    rowcount,
                    table,
                    column,
                    user_id,
                    pass_index + 1,
                )
        if not deleted_any:
            break

    return deleted_total


def _delete_rows_sql(table: str, column: str, user_id: int) -> int:
    quoted_table = connection.ops.quote_name(table)
    quoted_column = connection.ops.quote_name(column)
    with connection.cursor() as cursor:
        cursor.execute(
            f'DELETE FROM {quoted_table} WHERE {quoted_column} = %s',
            [user_id],
        )
        return cursor.rowcount


def _delete_user_sql(user_id: int) -> None:
    table = connection.ops.quote_name(User._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(f'DELETE FROM {table} WHERE id = %s', [user_id])
        if cursor.rowcount == 0:
            raise User.DoesNotExist()
