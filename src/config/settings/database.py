"""
Файл содержащий конфигурацию баз данных для Django-приложения.
Поддерживает множественные подключения к разным типам СУБД через YAML конфигурацию.
Использует централизованную объектно-ориентированную систему управления БД.
"""

import logging
import os
import sys

from src.config.settings.base import SYSTEM_DIR, RESOURCES_DIR
from src.config.env import env

from src.core.utils.database.config_manager import DjangoDatabaseConfigLoader

logger = logging.getLogger('config.database')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

_test_connections_env = env.bool('DATABASE_TEST_CONNECTIONS', default=True)
_argv_lower = ' '.join(getattr(sys, 'argv', [])).lower()
_is_autoreload_parent = (
    ('runserver' in _argv_lower or 'dev' in _argv_lower)
    and os.environ.get('RUN_MAIN') != 'true'
)
test_connections = _test_connections_env and not _is_autoreload_parent

db_loader = DjangoDatabaseConfigLoader(
    system_dir=SYSTEM_DIR,
    resources_dir=RESOURCES_DIR,
    test_connections=test_connections
)

# Загружаем конфигурацию БД
try:
    DATABASES = db_loader.load_config()
    logger.info(f"Загружено {len(DATABASES)} конфигураций БД: {', '.join(DATABASES.keys())}")
except Exception as e:
    logger.error(f"Критическая ошибка при загрузке БД: {str(e)}")
    # Fallback на SQLite
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': str(RESOURCES_DIR / 'db.sqlite3'),
        }
    }
    logger.warning("Используется fallback конфигурация SQLite")

from src.core.utils.database.module_schema import apply_search_path_options  # noqa: E402

DATABASES = apply_search_path_options(DATABASES)

# alias БД модуля: секция YAML с полем module: <имя_папки>
MODULE_DATABASE_ALIASES = {}
for _alias, _cfg in DATABASES.items():
    if not isinstance(_cfg, dict):
        continue
    # Django-конфиг не содержит module; смотрим raw YAML через extra ключ OPTIONS
    extra_module = (_cfg.get('MODULE') or _cfg.get('module') or '').strip()
    if extra_module:
        MODULE_DATABASE_ALIASES[extra_module] = _alias

DATABASE_ROUTERS = ['src.core.utils.database.routers.ModuleDatabaseRouter']