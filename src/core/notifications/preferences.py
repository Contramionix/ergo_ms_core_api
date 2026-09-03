"""
Резолвер предпочтений уведомлений и сервис панели настроек.

Приоритет для конкретного (event, channel):
1. Канал недоступен в каталоге -> False.
2. Глобальный master-switch канала (sentinel-строка '*'/'*') выключен -> False.
3. Явная запись NotificationPreference -> её значение.
4. Default канала из каталога событий.
"""

import logging

from src.core.integrations import bridge
from src.core.integrations.exceptions import BridgeUnavailable
from src.core.integrations.transports.user_identity import bridge_user_kwargs

from . import catalog
from .models import NotificationPreference, NotificationUserSettings

logger = logging.getLogger('core.notifications')

GLOBAL_KEY = NotificationPreference.GLOBAL_KEY

SIDEBAR_ACTIVITY_DAYS_MIN = NotificationUserSettings.SIDEBAR_ACTIVITY_DAYS_MIN
SIDEBAR_ACTIVITY_DAYS_MAX = NotificationUserSettings.SIDEBAR_ACTIVITY_DAYS_MAX
SIDEBAR_ACTIVITY_DAYS_DEFAULT = NotificationUserSettings.SIDEBAR_ACTIVITY_DAYS_DEFAULT

AUTO_ARCHIVE_DAYS_PRESETS = NotificationUserSettings.AUTO_ARCHIVE_DAYS_PRESETS
AUTO_ARCHIVE_DAYS_DEFAULT = NotificationUserSettings.AUTO_ARCHIVE_DAYS_DEFAULT


def clamp_sidebar_activity_days(value) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        return SIDEBAR_ACTIVITY_DAYS_DEFAULT
    return max(SIDEBAR_ACTIVITY_DAYS_MIN, min(SIDEBAR_ACTIVITY_DAYS_MAX, days))


def clamp_auto_archive_days(value) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        return AUTO_ARCHIVE_DAYS_DEFAULT
    if days in AUTO_ARCHIVE_DAYS_PRESETS:
        return days
    return min(AUTO_ARCHIVE_DAYS_PRESETS, key=lambda preset: abs(preset - days))


def get_sidebar_activity_days(user) -> int:
    """Число дней активности прочитанных в колокольчике (1–7)."""
    if user is None or not getattr(user, 'pk', None):
        return SIDEBAR_ACTIVITY_DAYS_DEFAULT
    try:
        settings_row = NotificationUserSettings.objects.only('sidebar_activity_days').get(
            user_id=user.pk,
        )
    except NotificationUserSettings.DoesNotExist:
        return SIDEBAR_ACTIVITY_DAYS_DEFAULT
    return clamp_sidebar_activity_days(settings_row.sidebar_activity_days)


def set_sidebar_activity_days(user, value) -> int:
    """Сохранить и вернуть clamped значение sidebar_activity_days."""
    days = clamp_sidebar_activity_days(value)
    NotificationUserSettings.objects.update_or_create(
        user_id=user.pk,
        defaults={'sidebar_activity_days': days},
    )
    return days


def get_auto_archive_days(user) -> int:
    """Срок автоархива прочитанных (пресеты 7/14/30/60/90)."""
    if user is None or not getattr(user, 'pk', None):
        return AUTO_ARCHIVE_DAYS_DEFAULT
    try:
        settings_row = NotificationUserSettings.objects.only('auto_archive_days').get(
            user_id=user.pk,
        )
    except NotificationUserSettings.DoesNotExist:
        return AUTO_ARCHIVE_DAYS_DEFAULT
    return clamp_auto_archive_days(settings_row.auto_archive_days)


def set_auto_archive_days(user, value) -> int:
    """Сохранить и вернуть clamped значение auto_archive_days."""
    days = clamp_auto_archive_days(value)
    NotificationUserSettings.objects.update_or_create(
        user_id=user.pk,
        defaults={'auto_archive_days': days},
    )
    return days


class PreferenceResolver:
    """Чтение эффективных настроек доставки для пользователя."""

    @staticmethod
    def _load_rows(user_id: int, source_module: str, event_key: str) -> dict:
        rows = NotificationPreference.objects.filter(
            user_id=user_id,
        ).filter(
            source_module__in=[GLOBAL_KEY, source_module or ''],
            event_key__in=[GLOBAL_KEY, event_key or ''],
        ).values_list('source_module', 'event_key', 'channel', 'enabled')
        return {(sm, ek, ch): enabled for sm, ek, ch, enabled in rows}

    @classmethod
    def get_enabled_channels(cls, user_id: int, *, source_module: str, event_key: str) -> dict:
        """Батч-резолв всех каналов одним запросом: {'in_app': bool, 'email': bool}."""
        defaults = catalog.get_channel_defaults(source_module, event_key)
        rows = cls._load_rows(user_id, source_module, event_key)

        result = {}
        for channel, spec in defaults.items():
            if not spec.get('available', False):
                result[channel] = False
                continue
            if rows.get((GLOBAL_KEY, GLOBAL_KEY, channel)) is False:
                result[channel] = False
                continue
            explicit = rows.get((source_module or '', event_key or '', channel))
            if explicit is not None:
                result[channel] = explicit
            else:
                result[channel] = bool(spec.get('default', False))
        return result

    @classmethod
    def is_enabled(cls, user_id: int, *, source_module: str, event_key: str, channel: str) -> bool:
        return cls.get_enabled_channels(
            user_id, source_module=source_module, event_key=event_key,
        ).get(channel, False)


class PreferencePanelService:
    """Сборка данных для панели настроек и применение изменений."""

    @staticmethod
    def get_global_switches(user_id: int) -> dict:
        rows = dict(
            NotificationPreference.objects.filter(
                user_id=user_id,
                source_module=GLOBAL_KEY,
                event_key=GLOBAL_KEY,
            ).values_list('channel', 'enabled')
        )
        return {channel: rows.get(channel, True) for channel in catalog.CHANNELS}

    @classmethod
    def build_sections(cls, user) -> dict:
        """Секции каталога с эффективными enabled-значениями для пользователя."""
        user_prefs = {
            (sm, ek, ch): enabled
            for sm, ek, ch, enabled in NotificationPreference.objects.filter(
                user_id=user.pk,
            ).values_list('source_module', 'event_key', 'channel', 'enabled')
        }

        sections = []
        for module, section in sorted(
            catalog.get_catalog().items(),
            key=lambda item: (item[0] != 'core', item[1]['module_label'].lower()),
        ):
            events = list(section['events'].values())
            events = cls._filter_events_for_user(module, events, user)
            if not events:
                continue

            categories = {}
            for event in events:
                cat_key = event['category']
                bucket = categories.setdefault(cat_key, {
                    'category': cat_key,
                    'category_label': event['category_label'],
                    'events': [],
                })
                bucket['events'].append(cls._build_event_row(event, module, user_prefs))

            sections.append({
                'module': module,
                'module_label': section['module_label'],
                'categories': list(categories.values()),
            })

        return {
            'global': cls.get_global_switches(user.pk),
            'sections': sections,
            'sidebar_activity_days': get_sidebar_activity_days(user),
            'auto_archive_days': get_auto_archive_days(user),
        }

    @staticmethod
    def _filter_events_for_user(module: str, events: list, user) -> list:
        """Опциональный per-module фильтр видимости строк каталога.

        Модуль может зарегистрировать операцию
        NOTIFICATIONS_FILTER_EVENTS_PREFIX + module (user, event_keys) -> list[str].
        """
        from src.core.integrations.module_contracts import NOTIFICATIONS_FILTER_EVENTS_PREFIX

        op = f'{NOTIFICATIONS_FILTER_EVENTS_PREFIX}{module}'
        try:
            if not bridge.has(op):
                return events
        except BridgeUnavailable:
            logger.warning('Фильтр каталога уведомлений %s: модуль не ответил', op)
            return events
        try:
            allowed = bridge.call(
                op,
                event_keys=[e['event_key'] for e in events],
                **bridge_user_kwargs(user),
            )
        except Exception:
            logger.exception('Фильтр каталога уведомлений %s упал', op)
            return events
        if allowed is None:
            return events
        allowed_set = set(allowed)
        return [e for e in events if e['event_key'] in allowed_set]

    @staticmethod
    def _build_event_row(event: dict, module: str, user_prefs: dict) -> dict:
        channels = {}
        for channel, spec in event['channels'].items():
            available = spec.get('available', False)
            explicit = user_prefs.get((module, event['event_key'], channel))
            enabled = explicit if explicit is not None else bool(spec.get('default', False))
            channels[channel] = {
                'available': available,
                'enabled': bool(enabled) if available else False,
            }
        return {
            'event_key': event['event_key'],
            'label': event['label'],
            'channels': channels,
        }

    @staticmethod
    def apply_patch(user, payload: dict) -> int:
        """Применить batch-изменения.

        Поддерживает:
        - global / items — каналы доставки;
        - sidebar_activity_days — окно колокольчика (1–7);
        - auto_archive_days — пресеты автоархива (7/14/30/60/90).

        Возвращает число upsert-ов (включая смену days-настроек).
        """
        updated = 0

        if 'sidebar_activity_days' in payload:
            set_sidebar_activity_days(user, payload.get('sidebar_activity_days'))
            updated += 1

        if 'auto_archive_days' in payload:
            set_auto_archive_days(user, payload.get('auto_archive_days'))
            updated += 1

        global_patch = payload.get('global') or {}
        for channel, enabled in global_patch.items():
            if channel not in catalog.CHANNELS or not isinstance(enabled, bool):
                continue
            NotificationPreference.objects.update_or_create(
                user_id=user.pk,
                source_module=GLOBAL_KEY,
                event_key=GLOBAL_KEY,
                channel=channel,
                defaults={'enabled': enabled},
            )
            updated += 1

        for item in payload.get('items') or []:
            if not isinstance(item, dict):
                continue
            source_module = item.get('source_module') or ''
            event_key = item.get('event_key') or ''
            channel = item.get('channel')
            enabled = item.get('enabled')
            if not event_key or channel not in catalog.CHANNELS or not isinstance(enabled, bool):
                continue
            spec = catalog.get_event_spec(source_module, event_key)
            if spec is None or not spec['channels'][channel].get('available', False):
                continue
            NotificationPreference.objects.update_or_create(
                user_id=user.pk,
                source_module=source_module,
                event_key=event_key,
                channel=channel,
                defaults={'enabled': enabled},
            )
            updated += 1

        return updated
