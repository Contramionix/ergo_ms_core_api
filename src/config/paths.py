"""
Пути проекта без зависимостей от Django settings и env.

Используется env.py до загрузки .env, чтобы избежать циклических импортов.
"""

from pathlib import Path

_CONFIG_DIR = Path(__file__).resolve().parent
_SRC_DIR = _CONFIG_DIR.parent
BASE_DIR = _SRC_DIR
API_DIR = _SRC_DIR.parent
# Репозиторный core/ (рядом с api/, client/, deployment/) — не Django-пакет src.core.
REPO_CORE_DIR = API_DIR.parent
CORE_DIR = REPO_CORE_DIR
SYSTEM_DIR = REPO_CORE_DIR.parent

ENV_FILE_PATH = SYSTEM_DIR / '.env'
ENV_FRAGMENTS_DIR = SYSTEM_DIR / 'env'
MODULES_DIR = SYSTEM_DIR / 'modules'
VIRTUAL_ENV_DIR = SYSTEM_DIR / 'virtual_env'
CACHE_DIR = VIRTUAL_ENV_DIR / 'cache'
DEPLOYMENT_DIR = REPO_CORE_DIR / 'deployment'

from src.config.log_paths import resolve_logs_root  # noqa: E402

LOGS_ROOT = resolve_logs_root(SYSTEM_DIR)
