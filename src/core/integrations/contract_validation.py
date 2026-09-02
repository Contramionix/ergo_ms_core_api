"""
Валидация схем platform-дескрипторов ModuleBridge.

Проверяет объекты, зарегистрированные через ``bridge.provide_many`` /
``provide_op`` для групп каталога ядра (локальный реестр процесса,
без HTTP к peer). Битые записи ловятся при ``django check`` / старте API
и в ``ergoms core-rules-check``, а не только когда каталог аудита или
session-claims молча отбрасывает дескриптор.

Режим ``BRIDGE_CONTRACTS``: ``off`` | ``warn`` | ``raise`` (см. settings/bridge.py).
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from django.conf import settings

from src.core.integrations import bridge
from src.core.integrations.module_contracts import (
    AUDIT_ACTION_DEFINITIONS_GROUP,
    AUDIT_SCOPE_DIMENSIONS_GROUP,
    CORE_COMPOSE_ADMIN_PASSWORD_RESET,
    CORE_COMPOSE_IMPORT_WELCOME_DEFAULTS,
    CORE_COMPOSE_PASSWORD_RESET_CODE,
    CORE_COMPOSE_REGISTRATION_INVITATION,
    MEDIA_UPLOAD_QUOTA_POLICIES_GROUP,
    NOTIFICATIONS_EMAIL_CONTEXT_GROUP,
    NOTIFICATIONS_EVENT_DEFINITIONS_GROUP,
    SESSION_CLAIMS_GROUP,
    SESSION_RESTORE_CLAIMS,
)

logger = logging.getLogger('integrations.bridge')

VALID_MODES = frozenset({'off', 'warn', 'raise'})

AUDIT_SEVERITIES = frozenset({'info', 'security', 'critical'})
NOTIFICATION_CHANNELS = frozenset({'in_app', 'email'})

_CHECK_REGISTERED = False


def _path(group: str, key: str, *parts: str) -> str:
    base = f'{group}[{key}]'
    if not parts:
        return base
    return base + ''.join(f'.{p}' for p in parts)


def _expect_dict(raw: Any, path: str, errors: list[str]) -> dict | None:
    if not isinstance(raw, dict):
        errors.append(f'{path}: ожидается dict, получено {type(raw).__name__}')
        return None
    return raw


def _expect_str(
    value: Any,
    path: str,
    errors: list[str],
    *,
    required: bool = True,
    allow_empty: bool = False,
) -> str | None:
    if value is None:
        if required:
            errors.append(f'{path}: обязательное строковое поле отсутствует')
        return None
    if not isinstance(value, str):
        errors.append(f'{path}: ожидается str, получено {type(value).__name__}')
        return None
    if not allow_empty and not value.strip():
        errors.append(f'{path}: пустая строка недопустима')
        return None
    return value


def _expect_bool(value: Any, path: str, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, bool):
        errors.append(f'{path}: ожидается bool, получено {type(value).__name__}')


def _expect_callable(value: Any, path: str, errors: list[str], *, required: bool) -> None:
    if value is None:
        if required:
            errors.append(f'{path}: обязательный callable отсутствует')
        return
    if not callable(value):
        errors.append(f'{path}: ожидается callable, получено {type(value).__name__}')


def _validate_session_claims(errors: list[str]) -> None:
    for key, raw in bridge.local_group(SESSION_CLAIMS_GROUP).items():
        path = _path(SESSION_CLAIMS_GROUP, str(key))
        data = _expect_dict(raw, path, errors)
        if data is None:
            continue

        _expect_str(data.get('claim'), f'{path}.claim', errors)

        if 'request_attr' in data and data.get('request_attr') is not None:
            _expect_str(
                data.get('request_attr'),
                f'{path}.request_attr',
                errors,
                required=False,
            )

        entity_key = data.get('entity_key')
        if entity_key is not None:
            _expect_str(entity_key, f'{path}.entity_key', errors, required=False)

        resolve_required = bool(entity_key)
        _expect_callable(
            data.get('resolve'),
            f'{path}.resolve',
            errors,
            required=resolve_required,
        )

        if 'required_guard' in data:
            _expect_bool(data.get('required_guard'), f'{path}.required_guard', errors)


def _validate_audit_actions(errors: list[str]) -> None:
    for key, raw in bridge.local_group(AUDIT_ACTION_DEFINITIONS_GROUP).items():
        path = _path(AUDIT_ACTION_DEFINITIONS_GROUP, str(key))
        data = _expect_dict(raw, path, errors)
        if data is None:
            continue

        _expect_str(data.get('module') or key, f'{path}.module', errors)
        if 'module_label' in data and data.get('module_label') is not None:
            _expect_str(
                data.get('module_label'),
                f'{path}.module_label',
                errors,
                required=False,
                allow_empty=True,
            )

        actions = data.get('actions')
        if actions is None:
            errors.append(f'{path}.actions: обязательное поле отсутствует')
            continue
        if not isinstance(actions, (list, tuple)):
            errors.append(
                f'{path}.actions: ожидается list, получено {type(actions).__name__}'
            )
            continue

        seen: set[str] = set()
        for idx, item in enumerate(actions):
            item_path = f'{path}.actions[{idx}]'
            action_data = _expect_dict(item, item_path, errors)
            if action_data is None:
                continue
            action = _expect_str(action_data.get('action'), f'{item_path}.action', errors)
            if action:
                if action in seen:
                    errors.append(f'{item_path}.action={action!r}: дубликат в секции')
                seen.add(action)
            if 'label' in action_data and action_data.get('label') is not None:
                _expect_str(
                    action_data.get('label'),
                    f'{item_path}.label',
                    errors,
                    required=False,
                    allow_empty=True,
                )
            severity = action_data.get('severity')
            if severity is not None:
                if not isinstance(severity, str) or severity not in AUDIT_SEVERITIES:
                    errors.append(
                        f'{item_path}.severity={severity!r}: допустимо '
                        f'{sorted(AUDIT_SEVERITIES)}'
                    )


def _validate_audit_dimensions(errors: list[str]) -> None:
    seen_keys: dict[str, str] = {}
    for key, raw in bridge.local_group(AUDIT_SCOPE_DIMENSIONS_GROUP).items():
        path = _path(AUDIT_SCOPE_DIMENSIONS_GROUP, str(key))
        data = _expect_dict(raw, path, errors)
        if data is None:
            continue

        dim_key = data.get('key') or key
        dim_key_str = _expect_str(dim_key, f'{path}.key', errors)
        if dim_key_str:
            if dim_key_str in seen_keys:
                errors.append(
                    f'{path}.key={dim_key_str!r}: дублирует измерение из '
                    f'{AUDIT_SCOPE_DIMENSIONS_GROUP}[{seen_keys[dim_key_str]}]'
                )
            else:
                seen_keys[dim_key_str] = str(key)

        if 'label' in data and data.get('label') is not None:
            _expect_str(
                data.get('label'),
                f'{path}.label',
                errors,
                required=False,
                allow_empty=True,
            )

        _expect_callable(data.get('resolve'), f'{path}.resolve', errors, required=True)

        if 'filter_param' in data and data.get('filter_param') is not None:
            _expect_str(
                data.get('filter_param'),
                f'{path}.filter_param',
                errors,
                required=False,
            )
        if 'indexed' in data:
            _expect_bool(data.get('indexed'), f'{path}.indexed', errors)
        if 'read_guard' in data:
            _expect_bool(data.get('read_guard'), f'{path}.read_guard', errors)


def _validate_notification_channel(raw: Any, path: str, errors: list[str]) -> None:
    data = _expect_dict(raw, path, errors)
    if data is None:
        return
    if 'available' in data:
        _expect_bool(data.get('available'), f'{path}.available', errors)
    if 'default' in data:
        _expect_bool(data.get('default'), f'{path}.default', errors)
    for field in ('subject', 'template_html', 'template_text'):
        if field in data and data.get(field) is not None and not isinstance(data.get(field), str):
            errors.append(
                f'{path}.{field}: ожидается str, получено {type(data.get(field)).__name__}'
            )


def _validate_notification_events(errors: list[str]) -> None:
    for key, raw in bridge.local_group(NOTIFICATIONS_EVENT_DEFINITIONS_GROUP).items():
        path = _path(NOTIFICATIONS_EVENT_DEFINITIONS_GROUP, str(key))
        data = _expect_dict(raw, path, errors)
        if data is None:
            continue

        _expect_str(data.get('module') or key, f'{path}.module', errors)
        if 'module_label' in data and data.get('module_label') is not None:
            _expect_str(
                data.get('module_label'),
                f'{path}.module_label',
                errors,
                required=False,
                allow_empty=True,
            )

        events = data.get('events')
        if events is None:
            errors.append(f'{path}.events: обязательное поле отсутствует')
            continue
        if not isinstance(events, (list, tuple)):
            errors.append(
                f'{path}.events: ожидается list, получено {type(events).__name__}'
            )
            continue

        seen: set[str] = set()
        for idx, item in enumerate(events):
            item_path = f'{path}.events[{idx}]'
            event = _expect_dict(item, item_path, errors)
            if event is None:
                continue
            event_key = _expect_str(event.get('event_key'), f'{item_path}.event_key', errors)
            if event_key:
                if event_key in seen:
                    errors.append(
                        f'{item_path}.event_key={event_key!r}: дубликат в секции'
                    )
                seen.add(event_key)

            channels = event.get('channels')
            if channels is None:
                continue
            channels_data = _expect_dict(channels, f'{item_path}.channels', errors)
            if channels_data is None:
                continue
            for channel_name, channel_spec in channels_data.items():
                if channel_name not in NOTIFICATION_CHANNELS:
                    errors.append(
                        f'{item_path}.channels.{channel_name}: неизвестный канал '
                        f'(допустимо {sorted(NOTIFICATION_CHANNELS)})'
                    )
                    continue
                _validate_notification_channel(
                    channel_spec,
                    f'{item_path}.channels.{channel_name}',
                    errors,
                )


def _validate_email_context(errors: list[str]) -> None:
    for key, raw in bridge.local_group(NOTIFICATIONS_EMAIL_CONTEXT_GROUP).items():
        path = _path(NOTIFICATIONS_EMAIL_CONTEXT_GROUP, str(key))
        _expect_callable(raw, path, errors, required=True)


def _validate_upload_quota_policies(errors: list[str]) -> None:
    from src.core.utils.media_upload_quota import (
        RESERVED_QUOTA_CLASSES,
        is_valid_quota_slug,
        is_valid_rate_string,
        normalize_policy_prefix,
    )

    for key, raw in bridge.local_group(MEDIA_UPLOAD_QUOTA_POLICIES_GROUP).items():
        path = _path(MEDIA_UPLOAD_QUOTA_POLICIES_GROUP, str(key))
        data = _expect_dict(raw, path, errors)
        if data is None:
            continue

        prefix = _expect_str(
            data.get('target_dir_prefix'),
            f'{path}.target_dir_prefix',
            errors,
        )
        if prefix and not normalize_policy_prefix(prefix):
            errors.append(
                f'{path}.target_dir_prefix={prefix!r}: ожидается относительный POSIX-префикс'
            )

        infix = data.get('path_must_contain')
        if infix is not None:
            _expect_str(
                infix,
                f'{path}.path_must_contain',
                errors,
                required=False,
            )

        quota = _expect_str(data.get('quota'), f'{path}.quota', errors)
        if quota:
            slug = quota.strip().lower()
            if slug in RESERVED_QUOTA_CLASSES or not is_valid_quota_slug(slug):
                errors.append(
                    f'{path}.quota={quota!r}: slug [a-z0-9_]+, не user/admin'
                )

        rate = data.get('rate')
        if rate is None:
            errors.append(f'{path}.rate: обязательное поле отсутствует')
        elif callable(rate):
            pass
        elif isinstance(rate, str):
            if not is_valid_rate_string(rate):
                errors.append(
                    f'{path}.rate={rate!r}: ожидается N/minute (second|hour|day)'
                )
        else:
            errors.append(
                f'{path}.rate: ожидается str или callable, получено {type(rate).__name__}'
            )

        _expect_callable(data.get('allows'), f'{path}.allows', errors, required=True)


_OPTIONAL_CALLABLE_OPS = (
    SESSION_RESTORE_CLAIMS,
    CORE_COMPOSE_REGISTRATION_INVITATION,
    CORE_COMPOSE_IMPORT_WELCOME_DEFAULTS,
    CORE_COMPOSE_PASSWORD_RESET_CODE,
    CORE_COMPOSE_ADMIN_PASSWORD_RESET,
)


def _validate_optional_callable_ops(errors: list[str]) -> None:
    # Только локальный реестр: has() по HTTP ретраит недоступный peer при django check.
    for name in _OPTIONAL_CALLABLE_OPS:
        handler = _resolve_single_provider(name)
        if handler is not None and not callable(handler):
            errors.append(
                f'{name}: провайдер должен быть callable, получено {type(handler).__name__}'
            )


def _resolve_single_provider(name: str) -> Any | None:
    """Достаёт handler single-op из локального реестра транспорта."""
    return bridge.local_providers().get(name)


def collect_contract_violations() -> list[str]:
    """Список нарушений схем platform-контрактов моста (пустой — всё ок)."""
    errors: list[str] = []
    _validate_session_claims(errors)
    _validate_audit_actions(errors)
    _validate_audit_dimensions(errors)
    _validate_notification_events(errors)
    _validate_email_context(errors)
    _validate_upload_quota_policies(errors)
    _validate_optional_callable_ops(errors)
    return errors


def get_contracts_mode() -> str:
    mode = (getattr(settings, 'BRIDGE_CONTRACTS', 'warn') or 'warn').strip().lower()
    if mode not in VALID_MODES:
        return 'warn'
    return mode


def report_contract_violations(
    violations: Iterable[str],
    *,
    mode: str | None = None,
) -> list[str]:
    """
    Логирует нарушения согласно режиму.

    Возвращает список нарушений (как есть). При ``raise`` бросает
    ``BridgeContractError``, если список непуст.
    """
    items = list(violations)
    if not items:
        return items

    effective = mode if mode is not None else get_contracts_mode()
    for msg in items:
        logger.warning('Контракт моста: %s', msg)

    if effective == 'raise':
        from src.core.integrations.exceptions import BridgeContractError

        raise BridgeContractError(
            'Обнаружены нарушения схем platform-контрактов ModuleBridge:\n'
            + '\n'.join(f'- {m}' for m in items)
        )
    return items


def register_bridge_contract_checks() -> None:
    """Регистрирует Django system check (идемпотентно)."""
    global _CHECK_REGISTERED
    if _CHECK_REGISTERED:
        return

    from django.core import checks

    @checks.register(checks.Tags.compatibility)
    def check_bridge_contracts(app_configs, **kwargs):  # noqa: ARG001
        mode = get_contracts_mode()
        if mode == 'off':
            return []

        from src.core.utils.django_cli import is_lean_schema_cli

        if is_lean_schema_cli():
            return []

        violations = collect_contract_violations()
        if not violations:
            return []

        messages = []
        for msg in violations:
            text = f'Контракт ModuleBridge: {msg}'
            if mode == 'raise':
                messages.append(checks.Error(text, id='integrations.E001'))
            else:
                messages.append(checks.Warning(text, id='integrations.W001'))
        return messages

    _CHECK_REGISTERED = True
