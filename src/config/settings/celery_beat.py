"""
Общая конфигурация Celery Beat.
Собирает расписания задач из всех модулей.
Использует централизованную объектно-ориентированную систему управления БД.
"""

import logging

from src.core.utils.celery_beat.manager import CeleryBeatModuleManager

# Импортируем централизованный менеджер БД для Celery Beat
from src.core.utils.database.config_manager import CeleryDatabaseConfigLoader
from src.config.settings.base import VIRTUAL_ENV_DIR, SYSTEM_DIR

logger = logging.getLogger('config.celery_beat')

# ==================== Конфигурация БД для Celery Beat ====================

# ЛОГИКА ВЫБОРА БРОКЕРА ДЛЯ BEAT:
# 1. Сначала пытаемся использовать общую секцию 'celery' (если есть)
# 2. Если нет общей, используем 'celery_worker' (чтобы совпадало с Worker)
# 3. Если нет 'celery_worker', используем 'celery_beat'
# 4. Если ничего нет - локальный SQLite
# Это обеспечивает, что Beat и Worker используют один брокер для обмена задачами

# Загрузчик для брокера Beat (приоритет: celery -> celery_worker -> celery_beat -> локальный)
beat_broker_loader = CeleryDatabaseConfigLoader(
    system_dir=SYSTEM_DIR,
    virtual_env_dir=VIRTUAL_ENV_DIR,
    section_priorities=['celery', 'celery_worker', 'celery_beat'],
    component_name="Celery Beat Broker"
)

# Загружаем конфигурацию брокера
beat_broker_config = beat_broker_loader.load_config()

# Настройки брокера и backend для Beat
CELERY_BEAT_BROKER_URL = beat_broker_config['broker_url']
CELERY_BEAT_RESULT_BACKEND = beat_broker_config['result_backend']

# ==================== Конфигурация scheduler для Beat ====================

# ЛОГИКА ВЫБОРА БД ДЛЯ РАСПИСАНИЯ:
# 1. Сначала пытаемся использовать 'celery_beat' (отдельная БД для расписания)
# 2. Если нет, используем общую 'celery'
# 3. Если ничего нет - локальный SQLite
# Расписание может храниться в отдельной БД, даже если брокер общий

# Загрузчик для расписания Beat (приоритет: celery_beat -> celery -> локальный)
beat_scheduler_loader = CeleryDatabaseConfigLoader(
    system_dir=SYSTEM_DIR,
    virtual_env_dir=VIRTUAL_ENV_DIR,
    section_priorities=['celery_beat', 'celery'],
    component_name="Celery Beat Scheduler"
)

# Загружаем конфигурацию расписания один раз
scheduler_config = beat_scheduler_loader.load_config()
db_alias = scheduler_config['section'] if scheduler_config['mode'] == 'database' else None

if db_alias is not None:
    CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'
    CELERY_BEAT_SCHEDULER_DB_ALIAS = db_alias
    logger.info(f"Celery Beat: Расписание хранится в БД '{db_alias}' (django-celery-beat)")
else:
    CELERY_BEAT_SCHEDULE_FILENAME = str(VIRTUAL_ENV_DIR / "celery" / "celerybeat-schedule.db")
    logger.info(f"Celery Beat: Расписание хранится в файле {CELERY_BEAT_SCHEDULE_FILENAME}")

# ==================== Расписание задач из модулей ====================

# Инициализируем менеджер Beat модулей
beat_module_manager = CeleryBeatModuleManager()

# Собираем все расписания из модулей
CELERY_BEAT_SCHEDULE = beat_module_manager.get_all_beat_schedules()

# Задачи ядра (журнал действий)
from celery.schedules import crontab
from src.config.env import env as _env

_audit_retention_days = _env.int('AUDIT_RETENTION_DAYS', default=0)
if _audit_retention_days > 0:
    CELERY_BEAT_SCHEDULE['audit-purge-old-events'] = {
        'task': 'core.audit.purge_old_events',
        'schedule': crontab(hour=3, minute=30),
    }

_client_monitoring_retention_days = _env.int('CLIENT_MONITORING_RETENTION_DAYS', default=7)
if _client_monitoring_retention_days > 0:
    CELERY_BEAT_SCHEDULE['client-monitor-purge-old'] = {
        'task': 'core.client_monitor.purge_old',
        'schedule': crontab(hour=3, minute=45),
    }

_session_device_retention_days = 0
try:
    from src.config.security_profile_runtime import session_device_retention_days as _sdrd

    _session_device_retention_days = _sdrd()
except Exception:
    _session_device_retention_days = _env.int('API_SESSION_DEVICE_RETENTION_DAYS', default=0)
if _session_device_retention_days > 0:
    CELERY_BEAT_SCHEDULE['session-device-purge-old'] = {
        'task': 'core.cms.adp.purge_old_devices',
        'schedule': crontab(hour=4, minute=0),
    }

CELERY_BEAT_SCHEDULE['notifications-archive-stale-read'] = {
    'task': 'core.notifications.archive_stale_read',
    'schedule': crontab(hour=4, minute=15),
}

CELERY_BEAT_SCHEDULE['integrations-flush-outbox'] = {
    'task': 'core.integrations.flush_outbox',
    'schedule': 30.0,
}

# Дополнительные настройки Beat из модулей
CELERY_BEAT_ADDITIONAL_CONFIG = beat_module_manager.get_additional_beat_configs()

# Логгеры Beat модулей
CELERY_BEAT_MODULE_LOGGERS = beat_module_manager.get_module_loggers()

# Список загруженных модулей Beat
CELERY_BEAT_MODULES = beat_module_manager.get_modules_list()

# ==================== Синхронизация задач с БД ====================
# Синхронизация будет выполнена в celery.py после полной инициализации Django 