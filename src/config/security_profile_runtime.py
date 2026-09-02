"""
Effective-скаляры профиля безопасности для Django settings.

Читает env → merge_security_profile_defaults → getters.
Профиль не пишет `.env` (см. no-auto-env-write).
"""

from __future__ import annotations

import os
import sys

from src.config.paths import DEPLOYMENT_DIR

if str(DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYMENT_DIR))

from ergo_modes import env_bool  # noqa: E402
from security.catalog import load_security_catalog  # noqa: E402
from security.profile_defaults import (  # noqa: E402
    APPLYABLE_CONTROL_IDS,
    merge_security_profile_defaults,
)


def _watched_keys() -> tuple[str, ...]:
    catalog = load_security_catalog()
    keys = ['ERGO_SECURITY']
    for control_id in APPLYABLE_CONTROL_IDS:
        control = catalog.control_by_id(control_id)
        if control and control.env_key:
            keys.append(control.env_key)
    return tuple(keys)


def _environ_subset() -> dict[str, str]:
    values: dict[str, str] = {}
    for key in _watched_keys():
        raw = os.environ.get(key)
        if raw is not None and str(raw).strip() != '':
            values[key] = str(raw).strip()
    return values


def merged_security_env() -> dict[str, str]:
    return merge_security_profile_defaults(_environ_subset())


def security_env_str(key: str, *, default: str) -> str:
    value = merged_security_env().get(key)
    if value is None or str(value).strip() == '':
        return default
    return str(value).strip()


def security_env_int(key: str, *, default: int) -> int:
    raw = security_env_str(key, default=str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def security_env_bool(key: str, *, default: bool) -> bool:
    merged = merged_security_env()
    if key not in merged or str(merged.get(key, '')).strip() == '':
        return default
    return env_bool(merged.get(key), default=default)


def login_throttle_rate() -> str:
    return security_env_str('API_THROTTLE_RATES_LOGIN', default='5/minute')


def password_min_length() -> int:
    return security_env_int('API_PASSWORD_MIN_LENGTH', default=8)


def jwt_lifetime_enabled() -> bool:
    return security_env_bool('API_JWT_LIFETIME_ENABLED', default=True)


def remember_me_refresh_token_lifetime() -> int:
    return security_env_int('API_REMEMBER_ME_REFRESH_TOKEN_LIFETIME', default=10080)


def media_url_expiration() -> int:
    return security_env_int('MEDIA_URL_EXPIRATION', default=3600)


def media_upload_rate() -> str:
    return security_env_str('MEDIA_API_UPLOAD_RATE', default='30/minute')


def media_upload_rate_admin() -> str:
    return security_env_str('MEDIA_API_UPLOAD_RATE_ADMIN', default='120/minute')


def media_upload_rate_ceiling() -> str:
    """Потолок rate в токене и nginx /upload/; не подменяет user/admin."""
    return security_env_str('MEDIA_API_UPLOAD_RATE_CEILING', default='1000/minute')


def client_browser_log_enabled() -> bool:
    return security_env_bool('CLIENT_BROWSER_LOG_ENABLED', default=True)


def adp_default_view_grants() -> str:
    """Auto `_view` for default role without groups: granted | denied."""
    raw = security_env_str('API_ADP_DEFAULT_VIEW_GRANTS', default='granted')
    mode = raw.strip().lower()
    if mode in ('granted', 'denied'):
        return mode
    return 'granted'


def csp_mode() -> str:
    """CSP mode: as_is | no_unsafe | no_unsafe_plus_externals."""
    from security.csp_policy import normalize_csp_mode

    return normalize_csp_mode(security_env_str('API_CSP_MODE', default='as_is'))


def auth_lockout_max_attempts() -> int:
    """0 = lockout выключен; hardened/maximum подставляют 10/5."""
    return security_env_int('API_AUTH_LOCKOUT_MAX_ATTEMPTS', default=0)


def session_device_retention_days() -> int:
    """0 = без автоочистки; hardened/maximum подставляют 90/30."""
    return security_env_int('API_SESSION_DEVICE_RETENTION_DAYS', default=0)
