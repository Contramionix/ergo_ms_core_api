"""
Ортогональные режимы ERGO_* для Django (обёртка над deployment/ergo_modes).
"""

from __future__ import annotations

import sys

from src.config.env import env
from src.config.paths import DEPLOYMENT_DIR

if str(DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYMENT_DIR))

from ergo_modes import (  # noqa: E402
    ERGO_BROKER_VALUES,
    ERGO_DB_VALUES,
    ERGO_EMAIL_VALUES,
    ERGO_ENV_VALUES,
    ERGO_JUPYTER_VALUES,
    ERGO_MEDIA_VALUES,
    ERGO_PROXY_VALUES,
    ERGO_REALTIME_VALUES,
    ERGO_RUNTIME_VALUES,
    apply_ergo_db_engine,
    default_engine_for_ergo_db,
    effective_deploy_type,
    effective_docker_enabled,
    effective_email_enabled,
    effective_jupyter_access_mode,
    effective_jupyter_behind_nginx,
    effective_jupyter_enabled,
    effective_media_access_mode,
    effective_nginx_enabled,
    effective_postgres_force_install,
    effective_realtime_transport,
    effective_redis_enabled,
    ergo_broker,
    ergo_db,
    ergo_email,
    ergo_env,
    ergo_jupyter,
    ergo_media,
    ergo_proxy,
    ergo_realtime,
    ergo_runtime,
    should_install_portable_postgres,
)


def _env_mapping() -> dict[str, str]:
    keys = (
        'ERGO_RUNTIME',
        'ERGO_PROXY',
        'ERGO_BROKER',
        'ERGO_DB',
        'ERGO_JUPYTER',
        'ERGO_EMAIL',
        'ERGO_MEDIA',
        'ERGO_ENV',
        'ERGO_REALTIME',
        'NGINX_ENABLED',
        'REDIS_ENABLED',
        'DOCKER_ENABLED',
        'POSTGRES_FORCE_INSTALL',
        'DOCKER_PROFILE_POSTGRES',
        'DOCKER_PROFILE_JUPYTER',
        'EMAIL_ENABLED',
        'MEDIA_ACCESS_MODE',
        'REALTIME_TRANSPORT',
        'API_DEPLOY_TYPE',
        'CLIENT_DEPLOY_TYPE',
        'MEDIA_API_DEPLOY_TYPE',
        'CELERY_BROKER_BACKEND',
        'API_CACHE_BACKEND',
        'CHANNEL_LAYER_BACKEND',
        'API_JUPYTER_ACCESS_MODE',
        'API_JUPYTER_BEHIND_NGINX',
        'API_JUPYTER_ALLOW_REMOTE',
    )
    values: dict[str, str] = {}
    for key in keys:
        raw = env.str(key, default='')
        if raw.strip() != '':
            values[key] = raw.strip()
    return values


def current_ergo_runtime() -> str:
    return ergo_runtime(_env_mapping())


def current_ergo_proxy() -> str:
    return ergo_proxy(_env_mapping())


def current_ergo_broker() -> str:
    return ergo_broker(_env_mapping())


def current_ergo_db() -> str:
    return ergo_db(_env_mapping())


def current_ergo_jupyter() -> str:
    return ergo_jupyter(_env_mapping())


def current_ergo_email() -> str:
    return ergo_email(_env_mapping())


def current_ergo_media() -> str:
    return ergo_media(_env_mapping())


def current_ergo_env() -> str:
    return ergo_env(_env_mapping())


def media_access_mode() -> str:
    return effective_media_access_mode(_env_mapping())


def current_ergo_realtime() -> str:
    return ergo_realtime(_env_mapping())


def realtime_transport() -> str:
    return effective_realtime_transport(_env_mapping())


def api_deploy_type() -> str:
    return effective_deploy_type(_env_mapping(), override_key='API_DEPLOY_TYPE')


def client_deploy_type() -> str:
    return effective_deploy_type(_env_mapping(), override_key='CLIENT_DEPLOY_TYPE')


def media_api_deploy_type() -> str:
    return effective_deploy_type(_env_mapping(), override_key='MEDIA_API_DEPLOY_TYPE')


def docker_enabled() -> bool:
    return effective_docker_enabled(_env_mapping())


def nginx_mode_enabled() -> bool:
    return effective_nginx_enabled(_env_mapping())


def redis_mode_enabled() -> bool:
    return effective_redis_enabled(_env_mapping())


def jupyter_mode_enabled() -> bool:
    return effective_jupyter_enabled(_env_mapping())


def jupyter_access_mode_from_ergo() -> str | None:
    return effective_jupyter_access_mode(_env_mapping())


def jupyter_behind_nginx_mode() -> bool:
    return effective_jupyter_behind_nginx(_env_mapping())


def email_mode_enabled() -> bool:
    return effective_email_enabled(_env_mapping())


def postgres_force_install() -> bool:
    return effective_postgres_force_install(_env_mapping())


def portable_postgres_requested() -> bool:
    return should_install_portable_postgres(_env_mapping())


def default_db_engine() -> str | None:
    return default_engine_for_ergo_db(_env_mapping())


def apply_default_db_engine(db_config: dict) -> dict:
    return apply_ergo_db_engine(db_config, _env_mapping())


__all__ = [
    'ERGO_BROKER_VALUES',
    'ERGO_DB_VALUES',
    'ERGO_EMAIL_VALUES',
    'ERGO_ENV_VALUES',
    'ERGO_JUPYTER_VALUES',
    'ERGO_MEDIA_VALUES',
    'ERGO_PROXY_VALUES',
    'ERGO_REALTIME_VALUES',
    'ERGO_RUNTIME_VALUES',
    'api_deploy_type',
    'apply_default_db_engine',
    'client_deploy_type',
    'current_ergo_broker',
    'current_ergo_db',
    'current_ergo_email',
    'current_ergo_env',
    'current_ergo_jupyter',
    'current_ergo_media',
    'current_ergo_proxy',
    'current_ergo_realtime',
    'current_ergo_runtime',
    'default_db_engine',
    'docker_enabled',
    'email_mode_enabled',
    'jupyter_access_mode_from_ergo',
    'jupyter_behind_nginx_mode',
    'jupyter_mode_enabled',
    'media_access_mode',
    'media_api_deploy_type',
    'nginx_mode_enabled',
    'portable_postgres_requested',
    'postgres_force_install',
    'realtime_transport',
    'redis_mode_enabled',
]
