import logging
import re
from datetime import timedelta
from typing import Any, Iterable

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from src.core.integrations import bridge
from src.core.realtime.hub import RealtimeHub
from src.core.realtime.topics import notifications_user_group, notifications_user_topic

from .channels_ import get_channels
from .models import Notification, NotificationUserSettings
from .navigation_validation import sanitize_notification_navigation, validate_notification_navigation
from .preferences import PreferenceResolver, clamp_auto_archive_days
from .unread_cache import (
    get_cached_unread_count,
    invalidate_unread_count_cache,
    set_cached_unread_count,
)

STALE_READ_ARCHIVE_DAYS = NotificationUserSettings.AUTO_ARCHIVE_DAYS_DEFAULT

logger = logging.getLogger('core.notifications')

User = get_user_model()

_HANDLER_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$')
_FORBIDDEN_HANDLER_PREFIXES = (
    'adp.',
    'core.',
    'session.',
    'audit.',
    'menu.',
    'media.',
    'notifications.',
)


def is_allowed_notification_action_handler(handler: str) -> bool:
    """True, если handler — dotted-имя и не привилегированная операция ядра."""
    if not isinstance(handler, str):
        return False
    name = handler.strip()
    if not name or not _HANDLER_NAME_RE.fullmatch(name):
        return False
    return not name.startswith(_FORBIDDEN_HANDLER_PREFIXES)


def _sanitize_notification_actions(actions: list) -> list:
    cleaned = []
    for item in actions:
        if not isinstance(item, dict):
            continue
        handler = item.get('handler') or ''
        if handler and not is_allowed_notification_action_handler(handler):
            logger.warning(
                'NotificationService.dispatch: отклонён handler %r',
                handler,
            )
            continue
        cleaned.append(item)
    return cleaned


def _resolve_recipient(recipient: Any):
    """Принимает User-инстанс или PK; возвращает (user_id, ok)."""
    if recipient is None:
        return None, False
    if isinstance(recipient, User):
        return recipient.pk, True
    if hasattr(recipient, 'pk') and isinstance(recipient.pk, int):
        return recipient.pk, True
    if isinstance(recipient, int):
        return recipient, True
    return None, False


class NotificationService:
    """Точка входа для создания и доставки уведомлений.

    Используется внутри ядра и через ModuleBridge (`notifications.create`)
    из любых модулей. Сначала создаётся (или возвращается существующая по
    `idempotency_key`) запись Notification, затем по очереди вызываются все
    зарегистрированные каналы доставки.
    """

    @staticmethod
    @transaction.atomic
    def dispatch(
        *,
        recipient: Any,
        title: str,
        body: str = '',
        level: str = Notification.LEVEL_INFO,
        icon: str = '',
        source_module: str = '',
        event_key: str = '',
        link_url: str | None = None,
        route: dict | None = None,
        meta: dict | None = None,
        actions: list | None = None,
        idempotency_key: str | None = None,
    ) -> Notification | None:
        recipient_id, ok = _resolve_recipient(recipient)
        if not ok:
            logger.warning('NotificationService.dispatch: некорректный recipient=%r', recipient)
            return None

        if not title or not isinstance(title, str):
            logger.warning('NotificationService.dispatch: пустой/некорректный title')
            return None

        nav_errors = validate_notification_navigation(link_url=link_url, route=route)
        if nav_errors:
            logger.warning(
                'NotificationService.dispatch: навигация санитизирована (%s)',
                '; '.join(nav_errors),
            )
        link_url, route = sanitize_notification_navigation(link_url=link_url, route=route)

        enabled_channels = PreferenceResolver.get_enabled_channels(
            recipient_id,
            source_module=source_module or '',
            event_key=event_key or '',
        )
        if not any(enabled_channels.values()):
            logger.debug(
                'NotificationService.dispatch: подавлено настройками user=%s %s.%s',
                recipient_id, source_module, event_key,
            )
            return None

        action_list = _sanitize_notification_actions(actions) if isinstance(actions, list) else []
        defaults = {
            'title': title,
            'body': body or '',
            'level': level or Notification.LEVEL_INFO,
            'icon': icon or '',
            'source_module': source_module or '',
            'event_key': event_key or '',
            'link_url': link_url or '',
            'route': route,
            'meta': meta or {},
            'actions': action_list,
            'actions_state': 'pending' if action_list else None,
            # email-only уведомление хранит данные для письма, но скрыто из inbox
            'in_app_visible': enabled_channels.get('in_app', True),
        }

        if idempotency_key:
            notification, created = Notification.objects.get_or_create(
                recipient_id=recipient_id,
                idempotency_key=idempotency_key,
                defaults=defaults,
            )
        else:
            notification = Notification.objects.create(
                recipient_id=recipient_id,
                idempotency_key=None,
                **defaults,
            )
            created = True

        for channel in get_channels().values():
            try:
                channel.deliver(notification, created=created)
            except Exception:
                logger.exception('Канал %s упал при доставке уведомления #%s',
                                 getattr(channel, 'name', channel), notification.pk)

        if notification.in_app_visible:
            invalidate_unread_count_cache(recipient_id)

        return notification

    @staticmethod
    def mark_read(notification_id: int, user) -> bool:
        try:
            notif = Notification.objects.get(
                pk=notification_id,
                recipient=user,
                deleted_at__isnull=True,
            )
        except Notification.DoesNotExist:
            return False
        if not notif.is_read:
            notif.is_read = True
            notif.read_at = timezone.now()
            notif.save(update_fields=['is_read', 'read_at'])
            invalidate_unread_count_cache(user.pk)
        return True

    @staticmethod
    def mark_all_read(user, *, source_module: str | None = None) -> int:
        qs = Notification.objects.filter(
            recipient=user,
            is_read=False,
            in_app_visible=True,
            deleted_at__isnull=True,
            archived_at__isnull=True,
        )
        if source_module:
            qs = qs.filter(source_module=source_module)
        updated = qs.update(
            is_read=True,
            read_at=timezone.now(),
        )
        if updated:
            invalidate_unread_count_cache(user.pk)
        return updated

    @staticmethod
    def unread_count(user) -> int:
        user_id = getattr(user, 'pk', None)
        if user_id is None:
            return 0

        cached = get_cached_unread_count(user_id)
        if cached is not None:
            return cached

        count = Notification.objects.filter(
            recipient=user,
            is_read=False,
            in_app_visible=True,
            deleted_at__isnull=True,
            archived_at__isnull=True,
        ).count()
        set_cached_unread_count(user_id, count)
        return count

    @staticmethod
    def _get_owned(notification_id: int, user) -> Notification | None:
        try:
            return Notification.objects.get(
                pk=notification_id,
                recipient=user,
                deleted_at__isnull=True,
            )
        except Notification.DoesNotExist:
            return None

    @staticmethod
    def archive(notification_id: int, user) -> Notification | None:
        notif = NotificationService._get_owned(notification_id, user)
        if notif is None:
            return None
        if notif.archived_at is None:
            notif.archived_at = timezone.now()
            if not notif.is_read:
                notif.is_read = True
                notif.read_at = notif.archived_at
                notif.save(update_fields=['archived_at', 'is_read', 'read_at'])
                invalidate_unread_count_cache(user.pk)
            else:
                notif.save(update_fields=['archived_at'])
        return notif

    @staticmethod
    def unarchive(notification_id: int, user) -> Notification | None:
        notif = NotificationService._get_owned(notification_id, user)
        if notif is None:
            return None
        if notif.archived_at is not None:
            now = timezone.now()
            notif.archived_at = None
            notif.inbox_restored_at = now
            notif.save(update_fields=['archived_at', 'inbox_restored_at'])
        return notif

    @staticmethod
    def _archive_stale_read_for_recipients(*, recipient_ids, older_than_days: int, now) -> int:
        if older_than_days <= 0 or not recipient_ids:
            return 0
        cutoff = now - timedelta(days=older_than_days)
        qs = Notification.objects.filter(
            recipient_id__in=recipient_ids,
            is_read=True,
            in_app_visible=True,
            archived_at__isnull=True,
            deleted_at__isnull=True,
        ).filter(
            Q(inbox_restored_at__isnull=True, created_at__lt=cutoff)
            | Q(inbox_restored_at__lt=cutoff)
        )
        return qs.update(archived_at=now)

    @classmethod
    def archive_stale_read(cls, *, older_than_days: int | None = None) -> int:
        """Автоархивация прочитанных по per-user сроку (или одному older_than_days)."""
        now = timezone.now()

        if older_than_days is not None:
            if older_than_days <= 0:
                return 0
            cutoff = now - timedelta(days=older_than_days)
            return Notification.objects.filter(
                is_read=True,
                in_app_visible=True,
                archived_at__isnull=True,
                deleted_at__isnull=True,
            ).filter(
                Q(inbox_restored_at__isnull=True, created_at__lt=cutoff)
                | Q(inbox_restored_at__lt=cutoff)
            ).update(archived_at=now)

        by_days: dict[int, list[int]] = {}
        configured_ids: list[int] = []
        for user_id, days in NotificationUserSettings.objects.values_list(
            'user_id', 'auto_archive_days',
        ):
            clamped = clamp_auto_archive_days(days)
            by_days.setdefault(clamped, []).append(user_id)
            configured_ids.append(user_id)

        total = 0
        for days, user_ids in by_days.items():
            total += cls._archive_stale_read_for_recipients(
                recipient_ids=user_ids,
                older_than_days=days,
                now=now,
            )

        default_days = STALE_READ_ARCHIVE_DAYS
        qs = Notification.objects.filter(
            is_read=True,
            in_app_visible=True,
            archived_at__isnull=True,
            deleted_at__isnull=True,
        )
        if configured_ids:
            qs = qs.exclude(recipient_id__in=configured_ids)
        cutoff = now - timedelta(days=default_days)
        total += qs.filter(
            Q(inbox_restored_at__isnull=True, created_at__lt=cutoff)
            | Q(inbox_restored_at__lt=cutoff)
        ).update(archived_at=now)
        return total

    @staticmethod
    def hide_from_sidebar(notification_id: int, user) -> Notification | None:
        notif = NotificationService._get_owned(notification_id, user)
        if notif is None:
            return None
        if notif.sidebar_hidden_at is None:
            now = timezone.now()
            notif.sidebar_hidden_at = now
            update_fields = ['sidebar_hidden_at']
            if not notif.is_read:
                notif.is_read = True
                notif.read_at = now
                update_fields.extend(['is_read', 'read_at'])
                notif.save(update_fields=update_fields)
                invalidate_unread_count_cache(user.pk)
            else:
                notif.save(update_fields=update_fields)
        return notif

    @staticmethod
    def recall(
        *,
        idempotency_key: str | None = None,
        idempotency_keys: Iterable[str] | None = None,
    ) -> int:
        """Отозвать уведомления по idempotency_key (модуль / система, не пользователь)."""
        keys: list[str] = []
        if idempotency_key and isinstance(idempotency_key, str):
            key = idempotency_key.strip()
            if key:
                keys.append(key)
        if idempotency_keys:
            for item in idempotency_keys:
                if isinstance(item, str):
                    key = item.strip()
                    if key and key not in keys:
                        keys.append(key)
        if not keys:
            return 0

        now = timezone.now()
        qs = Notification.objects.filter(
            idempotency_key__in=keys,
            deleted_at__isnull=True,
        )
        recipients_unread: set[int] = set()
        revoked: list[Notification] = []
        for notif in qs.iterator():
            was_unread = not notif.is_read and notif.archived_at is None
            notif.deleted_at = now
            update_fields = ['deleted_at']
            if not notif.is_read:
                notif.is_read = True
                notif.read_at = now
                update_fields.extend(['is_read', 'read_at'])
            notif.save(update_fields=update_fields)
            if was_unread:
                recipients_unread.add(notif.recipient_id)
            revoked.append(notif)

        for user_id in recipients_unread:
            invalidate_unread_count_cache(user_id)

        for notif in revoked:
            NotificationService._publish_revoked(notif)

        return len(revoked)

    @staticmethod
    def _publish_revoked(notification: Notification) -> None:
        try:
            user_id = notification.recipient_id
            RealtimeHub.publish(
                group=notifications_user_group(user_id),
                topic=notifications_user_topic(user_id),
                event_type='notification_revoked',
                payload={
                    'id': notification.pk,
                    'idempotency_key': notification.idempotency_key or '',
                    'deleted_at': notification.deleted_at.isoformat() if notification.deleted_at else None,
                },
            )
        except Exception:
            logger.exception(
                'Не удалось отправить realtime об отзыве уведомления #%s',
                notification.pk,
            )

    @staticmethod
    @transaction.atomic
    def execute_action(notification_id: int, user, action_id: str) -> dict:
        """Выполнить интерактивное действие уведомления через ModuleBridge."""
        try:
            notification = Notification.objects.select_for_update().get(
                pk=notification_id,
                recipient=user,
                in_app_visible=True,
                deleted_at__isnull=True,
            )
        except Notification.DoesNotExist:
            return {'success': False, 'error': 'not_found'}

        if notification.actions_state != 'pending':
            return {'success': False, 'error': 'not_pending'}

        action_def = None
        for item in notification.actions or []:
            if isinstance(item, dict) and item.get('id') == action_id:
                action_def = item
                break
        if not action_def:
            return {'success': False, 'error': 'invalid_action'}

        handler = action_def.get('handler') or ''
        if not is_allowed_notification_action_handler(handler) or not bridge.has(handler):
            logger.warning(
                'NotificationService.execute_action: handler %r не зарегистрирован',
                handler,
            )
            return {'success': False, 'error': 'handler_unavailable'}

        try:
            result = bridge.call(
                handler,
                notification=notification,
                action_id=action_id,
                user=user,
            )
        except Exception:
            logger.exception(
                'NotificationService.execute_action: handler %s упал для #%s',
                handler,
                notification_id,
            )
            return {'success': False, 'error': 'handler_failed'}

        if not isinstance(result, dict) or not result.get('success'):
            return {
                'success': False,
                'error': result.get('error', 'handler_rejected') if isinstance(result, dict) else 'handler_rejected',
                'message': result.get('message', '') if isinstance(result, dict) else '',
            }

        now = timezone.now()
        update_fields = [
            'actions_state',
            'resolved_action_id',
            'resolved_at',
            'is_read',
            'read_at',
        ]
        notification.actions_state = 'resolved'
        notification.resolved_action_id = action_id
        notification.resolved_at = now
        notification.is_read = True
        notification.read_at = now

        update_payload = result.get('update') if isinstance(result.get('update'), dict) else {}
        if update_payload.get('body') is not None:
            notification.body = update_payload['body']
            update_fields.append('body')
        if update_payload.get('level'):
            notification.level = update_payload['level']
            update_fields.append('level')

        notification.save(update_fields=update_fields)
        invalidate_unread_count_cache(user.pk)

        return {
            'success': True,
            'message': result.get('message', ''),
            'notification': notification,
            'unread_count': NotificationService.unread_count(user),
        }
