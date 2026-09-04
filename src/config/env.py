"""
Загрузка переменных окружения для Django.

Порядок:
1. Корневой .env
2. Фрагменты env/*.env (nginx.env, docker.env, …)
3. modules/**/.env (перекрывают корень и фрагменты)
"""

import logging
import os
import sys

import environ

from src.config.paths import DEPLOYMENT_DIR, ENV_FILE_PATH, SYSTEM_DIR
from src.core.utils.environment.methods import collect_env_files_from_all_sources

logger = logging.getLogger(__name__)

if str(DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYMENT_DIR))

from env_file_loader import apply_project_env_to_environ, list_fragment_env_files  # noqa: E402

env = environ.Env()

main_env_vars = set()
loaded = apply_project_env_to_environ(SYSTEM_DIR, override_existing=False)
main_env_vars.update(loaded.keys())

if ENV_FILE_PATH.exists():
    logger.info('Загружен основной .env: %s', ENV_FILE_PATH)
else:
    logger.warning('Файл .env не найден: %s', ENV_FILE_PATH)

fragments = list_fragment_env_files(SYSTEM_DIR)
if fragments:
    logger.info(
        'Загружены фрагменты env/: %s',
        ', '.join(path.name for path in fragments),
    )
logger.info('Найдено переменных (корень + env/): %d', len(main_env_vars))

modules_env_vars = collect_env_files_from_all_sources()

if modules_env_vars:
    overridden_vars = []
    for key, value in modules_env_vars.items():
        if key in main_env_vars:
            overridden_vars.append(key)
        os.environ[key] = value

    if overridden_vars:
        logger.warning(
            'Переменные из modules переопределили %d переменных из корня/env/:',
            len(overridden_vars),
        )
        for var in overridden_vars:
            logger.warning(' - %s', var)

from no_proxy_hosts import apply_effective_no_proxy_to_environ  # noqa: E402

apply_effective_no_proxy_to_environ()
