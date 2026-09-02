"""Выбор класса квоты загрузки media_api по политикам модулей.

Ядро не знает имена модулей: политики приходят из
``media.upload_quota_policies``. Клиентский ``quota`` игнорируется.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from src.config.security_profile_runtime import (
    media_upload_rate_admin,
    media_upload_rate_ceiling,
)
from src.core.cms.adp.services.permissions import PermissionService
from src.core.integrations import bridge
from src.core.integrations.module_contracts import MEDIA_UPLOAD_QUOTA_POLICIES_GROUP
from src.core.utils.media_upload_validation import normalize_target_dir

logger = logging.getLogger('utils.media_upload_quota')

RESERVED_QUOTA_CLASSES = frozenset({'user', 'admin'})
_QUOTA_SLUG_RE = re.compile(r'^[a-z][a-z0-9_]{0,62}$')
_RATE_RE = re.compile(r'^(\d+)/(second|minute|hour|day)$', re.IGNORECASE)
_RATE_WINDOWS = {'second': 1.0, 'minute': 60.0, 'hour': 3600.0, 'day': 86400.0}


@dataclass(frozen=True)
class ResolvedUploadQuota:
    """Итоговый класс квоты для upload-токена."""

    quota: str
    rate: str | None = None


def is_valid_quota_slug(value: str) -> bool:
    slug = (value or '').strip().lower()
    return bool(_QUOTA_SLUG_RE.match(slug)) and slug not in RESERVED_QUOTA_CLASSES


def is_valid_rate_string(value: str) -> bool:
    return bool(_RATE_RE.match((value or '').strip()))


def parse_upload_rate(rate: str) -> tuple[int, float]:
    """N/unit → (limit, window_seconds). При ошибке — 30/minute."""
    match = _RATE_RE.match((rate or '').strip().lower())
    if not match:
        return 30, 60.0
    return int(match.group(1)), _RATE_WINDOWS.get(match.group(2), 60.0)


def rate_per_second(rate: str) -> float:
    limit, window = parse_upload_rate(rate)
    if window <= 0:
        return 0.0
    return limit / window


def higher_upload_rate(first: str, second: str) -> str:
    """Строка частоты с большей скоростью (при равенстве — first)."""
    if not is_valid_rate_string(first):
        return second if is_valid_rate_string(second) else '30/minute'
    if not is_valid_rate_string(second):
        return first
    if rate_per_second(second) > rate_per_second(first):
        return second.strip()
    return first.strip()


def default_upload_rate_ceiling() -> str:
    raw = (media_upload_rate_ceiling() or '').strip()
    if is_valid_rate_string(raw):
        return raw
    return '1000/minute'


def cap_upload_rate(rate: str, *, ceiling: str | None = None) -> str:
    """Не выше MEDIA_API_UPLOAD_RATE_CEILING."""
    cap = ceiling if ceiling is not None else default_upload_rate_ceiling()
    if not is_valid_rate_string(cap):
        cap = '1000/minute'
    if not is_valid_rate_string(rate):
        return cap
    if rate_per_second(rate) > rate_per_second(cap):
        return cap
    return rate.strip()


def normalize_policy_prefix(prefix: str) -> str:
    raw = (prefix or '').replace('\\', '/').strip().strip('/')
    if not raw or '..' in raw.split('/'):
        return ''
    return raw


def _infix_matches(path: str, infix: str) -> bool:
    needle = (infix or '').replace('\\', '/').strip()
    if not needle:
        return True
    haystack = f'/{path.strip("/")}/'
    return needle in haystack


def _prefix_matches(path: str, prefix: str) -> bool:
    if not prefix:
        return False
    if path == prefix:
        return True
    return path.startswith(f'{prefix}/')


def _policy_rate(policy: dict[str, Any]) -> str | None:
    raw = policy.get('rate')
    if callable(raw):
        try:
            raw = raw()
        except Exception:
            logger.warning('Не удалось вычислить rate политики квоты загрузки', exc_info=True)
            return None
    if not isinstance(raw, str) or not is_valid_rate_string(raw):
        return None
    return raw.strip()


def _collect_matching_policies(path: str) -> list[tuple[int, int, dict[str, Any]]]:
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for raw in bridge.all(MEDIA_UPLOAD_QUOTA_POLICIES_GROUP).values():
        if not isinstance(raw, dict):
            continue
        prefix = normalize_policy_prefix(str(raw.get('target_dir_prefix') or ''))
        if not prefix or not _prefix_matches(path, prefix):
            continue
        infix = raw.get('path_must_contain')
        has_infix = bool(isinstance(infix, str) and infix.strip())
        if has_infix and not _infix_matches(path, str(infix)):
            continue
        ranked.append((len(prefix), 1 if has_infix else 0, raw))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return ranked


def _fallback_quota(user) -> ResolvedUploadQuota:
    if PermissionService.is_admin(user):
        return ResolvedUploadQuota(quota='admin')
    return ResolvedUploadQuota(quota='user')


def _policy_allows(policy: dict[str, Any], user) -> bool:
    allows = policy.get('allows')
    if callable(allows):
        try:
            return bool(allows(user))
        except Exception:
            logger.warning('allows политики квоты загрузки завершился с ошибкой', exc_info=True)
            return False
    module = policy.get('allows_module')
    keys = policy.get('allows_keys')
    if isinstance(module, str) and module.strip() and isinstance(keys, (list, tuple)):
        names = [str(key) for key in keys if str(key).strip()]
        if names:
            return bool(allows_module_permission(module.strip(), *names)(user))
    return False


def resolve_upload_quota(*, user, target_dir: str | None) -> ResolvedUploadQuota:
    """
    Класс квоты для токена: политика модуля или user/admin.

    Глобальный админ на совпавшем prefix получает класс модуля и
    max(policy.rate, MEDIA_API_UPLOAD_RATE_ADMIN).
    """
    path = normalize_target_dir(target_dir)
    if not path:
        return _fallback_quota(user)

    matches = _collect_matching_policies(path)
    if not matches:
        return _fallback_quota(user)

    policy = matches[0][2]
    quota = str(policy.get('quota') or '').strip().lower()
    rate = _policy_rate(policy)
    if not is_valid_quota_slug(quota) or rate is None:
        logger.warning('Политика квоты загрузки пропущена: некорректные quota/rate')
        return _fallback_quota(user)

    admin_rate = media_upload_rate_admin()
    if PermissionService.is_admin(user):
        chosen = higher_upload_rate(rate, admin_rate)
        return ResolvedUploadQuota(quota=quota, rate=cap_upload_rate(chosen))

    if _policy_allows(policy, user):
        return ResolvedUploadQuota(quota=quota, rate=cap_upload_rate(rate))

    return _fallback_quota(user)


def env_upload_rate(env_key: str, default: str) -> str:
    """Частота из env процесса (модульный .env уже в окружении)."""
    raw = (os.environ.get(env_key) or '').strip()
    if is_valid_rate_string(raw):
        return raw
    return default if is_valid_rate_string(default) else '30/minute'


def allows_authenticated(user) -> bool:
    return bool(user and getattr(user, 'is_authenticated', False))


def allows_module_permission(module_name: str, *permission_keys: str):
    """``allows`` для политики: любое из прав модуля (админ — через resolve)."""
    keys = tuple(permission_keys)

    def _allows(user) -> bool:
        if not allows_authenticated(user):
            return False
        return any(
            PermissionService.check_module_permission(user, module_name, key)
            for key in keys
        )

    _allows._bridge_json = {
        'allows_module': module_name,
        'allows_keys': list(keys),
    }
    return _allows
