"""
Файл содержащий конфигурацию Celery для Django-приложения.
Включает настройки брокера сообщений и результатов задач с поддержкой 
различных СУБД из databases.yaml или локального SQLite.
Использует централизованную объектно-ориентированную систему управления БД.

Настройки:
    CELERY_BROKER_URL: URL-адрес брокера сообщений
    CELERY_RESULT_BACKEND: URL-адрес бэкенда для хранения результатов задач

    CELERY_ACCEPT_CONTENT: Список разрешенных форматов сериализации
    CELERY_TASK_SERIALIZER: Формат сериализации для задач
    CELERY_RESULT_SERIALIZER: Формат сериализации для результатов
    CELERY_TIMEZONE: Временная зона для планировщика задач
    CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP: Флаг повторного подключения к брокеру при запуске

Режимы работы:
    - ERGO_BROKER=redis / CELERY_BROKER_BACKEND=redis — брокер и results в Redis (секция redis в databases.yaml)
    - CELERY_BROKER_BACKEND=auto (по умолчанию): ERGO_BROKER=redis → Redis; иначе celery* в yaml → SQLite
    - CELERY_BROKER_BACKEND=database — только секции databases.yaml
    - CELERY_BROKER_BACKEND=local / ERGO_BROKER=local — локальный SQLite
    - Секции databases.yaml: celery_worker / celery / celery_beat (SQL-брокер)
"""

import os

from src.config.redis_runtime import (
    sanitize_celery_redis_url,
    ensure_kombu_redis_resp2,
    uses_redis_celery_backend,
)
from src.config.settings.base import SYSTEM_DIR, VIRTUAL_ENV_DIR
from src.config.settings.localization import TIME_ZONE as CELERY_TIMEZONE

for _env_key in ('CELERY_BROKER_URL', 'CELERY_RESULT_BACKEND'):
    _raw_url = os.environ.get(_env_key, '').strip()
    if _raw_url:
        os.environ[_env_key] = sanitize_celery_redis_url(_raw_url)

# Импортируем централизованный менеджер БД для Celery
from src.core.utils.database.config_manager import CeleryDatabaseConfigLoader

# ==================== Конфигурация для Celery Worker ====================

# Создаем загрузчик для Celery Worker (приоритет: celery_worker -> celery -> локальный)
worker_loader = CeleryDatabaseConfigLoader(
    system_dir=SYSTEM_DIR,
    virtual_env_dir=VIRTUAL_ENV_DIR,
    section_priorities=['celery_worker', 'celery'],
    component_name="Celery Worker"
)

# Загружаем конфигурацию
worker_config = worker_loader.load_config()

# Устанавливаем переменные для Django settings
CELERY_BROKER_URL = worker_config['broker_url']
CELERY_RESULT_BACKEND = worker_config['result_backend']

if uses_redis_celery_backend(CELERY_BROKER_URL, CELERY_RESULT_BACKEND):
    ensure_kombu_redis_resp2()

# ==================== Общие настройки Celery ====================

CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
# CELERY_TIMEZONE — из localization.TIME_ZONE (.env TIME_ZONE).
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True