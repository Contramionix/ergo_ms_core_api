"""
Основной конфигурационный файл Celery для Django-приложения.

Отвечает за:
- инициализацию Celery-приложения;
- интеграцию с Django settings;
- автообнаружение задач;
- модульную конфигурацию очередей и маршрутов;
- настройку Celery Beat.
"""

import logging
import os
import sys
import threading
from typing import Any, Dict

from celery import Celery
from django.conf import settings

from src.config.log_format import (
    CELERY_WORKER_LOG_FORMAT,
    CELERY_WORKER_TASK_LOG_FORMAT,
)
from src.core.utils.auto_api.auto_config import get_env_deploy_type
from src.core.utils.log_i18n import log_t

# ---------------------------------------------------------------------------
# БАЗОВАЯ НАСТРОЙКА DJANGO SETTINGS ДЛЯ CELERY
# ---------------------------------------------------------------------------

deploy_type = get_env_deploy_type()
os.environ.setdefault("DJANGO_SETTINGS_MODULE", deploy_type)

logger = logging.getLogger("config.celery")

# Определяем тип процесса по отдельным аргументам (не substring в объединённой строке)
_argv_lower = {arg.lower() for arg in sys.argv}
IS_BEAT = "beat" in _argv_lower
IS_CELERY_PROCESS = bool(_argv_lower & {"worker", "beat"})

# ---------------------------------------------------------------------------
# СОЗДАНИЕ CELERY ПРИЛОЖЕНИЯ (ЛЁГКАЯ ОПЕРАЦИЯ)
# ---------------------------------------------------------------------------

celery_app = Celery("src")
# Иначе @shared_task в API (daphne) цепляется к анонимному Celery("default") → amqp://localhost.
celery_app.set_default()


# ---------------------------------------------------------------------------
# ПОЛНАЯ КОНФИГУРАЦИЯ CELERY — ТОЛЬКО ДЛЯ CELERY ПРОЦЕССОВ
# ---------------------------------------------------------------------------

MODULE_MANAGER = None
MODULE_TASK_ROUTES: Dict = {}
MODULE_TASK_QUEUES: Dict = {}

if IS_CELERY_PROCESS:
    from src.config.redis_runtime import sanitize_celery_redis_url
    from src.core.utils.celery.manager import CeleryModuleManager

    for _env_key in ('CELERY_BROKER_URL', 'CELERY_RESULT_BACKEND'):
        _raw_url = os.environ.get(_env_key, '').strip()
        if _raw_url:
            os.environ[_env_key] = sanitize_celery_redis_url(_raw_url)

    # Конфигурация Celery из Django settings
    if IS_BEAT:
        logger.info("Celery: запуск BEAT, используются настройки CELERY_BEAT_*")
        celery_app.config_from_object("django.conf:settings", namespace="CELERY_BEAT")

        # Дополнительно применяем CELERY_* для совместимости
        from django.conf import settings as django_settings

        for key in dir(django_settings):
            if key.startswith("CELERY_") and not key.startswith("CELERY_BEAT_"):
                celery_app.conf[key.replace("CELERY_", "").lower()] = getattr(django_settings, key)
    else:
        logger.info("Celery: запуск WORKER, используются настройки CELERY_*")
        celery_app.config_from_object("django.conf:settings", namespace="CELERY")

    from django.conf import settings as django_settings

    if hasattr(django_settings, 'CELERY_BROKER_URL'):
        celery_app.conf.broker_url = django_settings.CELERY_BROKER_URL
    if hasattr(django_settings, 'CELERY_RESULT_BACKEND'):
        celery_app.conf.result_backend = django_settings.CELERY_RESULT_BACKEND

    # Автообнаружение задач во всех приложениях
    celery_app.autodiscover_tasks(lambda: settings.INSTALLED_APPS)

    # Worker/beat читают bin-кэш; полный импорт celery_config — только при промахе.
    MODULE_MANAGER = CeleryModuleManager(use_config_cache=True)
    from src.core.utils.celery.startup_format import celery_startup_verbose, format_name_list

    _celery_modules = MODULE_MANAGER.get_modules_list()
    logger.info(
        "Celery: загружено %d модулей: %s",
        len(_celery_modules),
        format_name_list(_celery_modules, verbose=celery_startup_verbose()),
    )

    MODULE_TASK_ROUTES = MODULE_MANAGER.get_all_task_routes()
    MODULE_TASK_QUEUES = MODULE_MANAGER.get_all_task_queues()

    # Настройка параллелизма по очередям (только для worker'ов)
    if not IS_BEAT:
        MODULE_MANAGER.setup_queue_concurrency()
        queue_limits = MODULE_MANAGER.get_all_queue_limits()
        if queue_limits:
            from src.core.utils.celery.concurrency import (
                queue_concurrency_manager,
                setup_concurrency_limited_tasks,
            )

            cache_backend = getattr(settings, 'CACHE_BACKEND', 'file')
            if cache_backend == 'redis':
                queue_concurrency_manager.configure(use_distributed=True)
                logger.info(
                    "Celery: распределённый лимит параллелизма (Redis cache)"
                )

            setup_concurrency_limited_tasks(celery_app)

    # Очередь по умолчанию
    if "default" not in MODULE_TASK_QUEUES:
        MODULE_TASK_QUEUES["default"] = {"exchange": "default", "routing_key": "default"}

    if "*" not in MODULE_TASK_ROUTES and "default" in MODULE_TASK_QUEUES:
        MODULE_TASK_ROUTES["*"] = {"queue": "default"}

    # -----------------------------------------------------------------------
    # Общая конфигурация для Worker и Beat
    # -----------------------------------------------------------------------

    def _build_common_celery_config(
        manager: 'CeleryModuleManager',
        task_routes: Dict,
        task_queues: Dict,
    ) -> Dict[str, Any]:
        config: Dict[str, Any] = {
            "task_routes": task_routes,
            "task_default_queue": "default",
            "task_queues": task_queues,
            "task_annotations": manager.get_all_task_annotations(),
            "task_acks_late": True,
            "worker_log_format": CELERY_WORKER_LOG_FORMAT,
            "worker_task_log_format": CELERY_WORKER_TASK_LOG_FORMAT,
            "worker_log_color": False,
            "worker_redirect_stdouts": False,
            "worker_redirect_stdouts_level": "INFO",
        }
        config.update(manager.get_additional_configs())
        return config

    if not IS_BEAT and MODULE_MANAGER is not None:
        celery_app.conf.update(
            **_build_common_celery_config(MODULE_MANAGER, MODULE_TASK_ROUTES, MODULE_TASK_QUEUES),
        )
        from src.core.utils.celery.footprint_history import connect_signals
        from src.core.utils.celery.overlay_limits import start_overlay_limit_watcher

        connect_signals(celery_app)
        start_overlay_limit_watcher()
        try:
            from src.core.utils.request_id import get_request_id, set_request_id
            from celery.signals import before_task_publish, task_prerun

            @before_task_publish.connect
            def _attach_request_id_header(headers=None, **_):
                if headers is None:
                    return
                token = get_request_id()
                if token:
                    headers['ergo_request_id'] = token

            @task_prerun.connect
            def _restore_request_id(task=None, **_):
                headers = getattr(getattr(task, 'request', None), 'headers', None) or {}
                token = headers.get('ergo_request_id') or ''
                if token:
                    set_request_id(str(token))
        except Exception:
            logger.debug('request_id celery signals skipped', exc_info=True)

    # -----------------------------------------------------------------------
    # Настройки Celery Beat
    # -----------------------------------------------------------------------

    CELERY_BEAT_SCHEDULER = None
    CELERY_BEAT_SCHEDULER_DB_ALIAS = None
    CELERY_BEAT_SCHEDULE_FILENAME = str(
        settings.VIRTUAL_ENV_DIR / "celery" / "celerybeat-schedule.db"
    )
    CELERY_BEAT_SCHEDULE = {}

    if IS_BEAT:
        try:
            import src.config.settings.celery_beat as beat_settings

            CELERY_BEAT_SCHEDULER = getattr(beat_settings, "CELERY_BEAT_SCHEDULER", None)
            CELERY_BEAT_SCHEDULER_DB_ALIAS = getattr(
                beat_settings, "CELERY_BEAT_SCHEDULER_DB_ALIAS", None
            )
            CELERY_BEAT_SCHEDULE_FILENAME = getattr(
                beat_settings,
                "CELERY_BEAT_SCHEDULE_FILENAME",
                CELERY_BEAT_SCHEDULE_FILENAME,
            )
            CELERY_BEAT_SCHEDULE = getattr(beat_settings, "CELERY_BEAT_SCHEDULE", {})

            if CELERY_BEAT_SCHEDULE:
                _beat_tasks = list(CELERY_BEAT_SCHEDULE.keys())
                logger.info(
                    "Beat: %d расписаний: %s",
                    len(_beat_tasks),
                    format_name_list(_beat_tasks, max_show=5, verbose=celery_startup_verbose()),
                )
            else:
                logger.info("Beat: расписаний нет")
        except Exception as exc:
            logger.error("Beat: ошибка загрузки расписаний: %s", exc)

    beat_scheduler_config: Dict = {}
    if IS_BEAT:
        if CELERY_BEAT_SCHEDULER:
            beat_scheduler_config["beat_scheduler"] = CELERY_BEAT_SCHEDULER
            if CELERY_BEAT_SCHEDULER_DB_ALIAS:
                beat_scheduler_config["beat_scheduler_db_alias"] = CELERY_BEAT_SCHEDULER_DB_ALIAS
        else:
            beat_scheduler_config["beat_schedule_filename"] = CELERY_BEAT_SCHEDULE_FILENAME

        if CELERY_BEAT_SCHEDULE:
            beat_scheduler_config["beat_schedule"] = CELERY_BEAT_SCHEDULE

        if MODULE_MANAGER is not None:
            celery_app.conf.update(
                **beat_scheduler_config,
                **_build_common_celery_config(MODULE_MANAGER, MODULE_TASK_ROUTES, MODULE_TASK_QUEUES),
            )

    # -----------------------------------------------------------------------
    # Синхронизация задач Beat с БД (django-celery-beat) — в фоне, не блокирует старт
    # -----------------------------------------------------------------------

    if IS_BEAT and CELERY_BEAT_SCHEDULER and CELERY_BEAT_SCHEDULER_DB_ALIAS:
        import threading

        def _run_beat_sync() -> None:
            try:
                import django
                from django.apps import apps

                if not apps.ready:
                    django.setup()

                from src.core.utils.celery_beat.sync import CeleryBeatSyncManager

                sync_manager = CeleryBeatSyncManager(
                    config_schedule=CELERY_BEAT_SCHEDULE or {},
                    db_alias=CELERY_BEAT_SCHEDULER_DB_ALIAS,
                )
                sync_results = sync_manager.sync_all()
                logger.info(
                    "Beat: синхронизация с БД завершена - создано: %s, обновлено: %s, удалено: %s",
                    sync_results["created"],
                    sync_results["updated"],
                    sync_results["deleted"],
                )
            except Exception as exc:
                logger.error("Beat: ошибка синхронизации задач с БД: %s", exc, exc_info=True)

        logger.info("Beat: запуск синхронизации с БД в фоне...")
        _sync_thread = threading.Thread(target=_run_beat_sync, daemon=True)
        _sync_thread.start()

    def ensure_django_celery_configured() -> None:
        """Worker/beat уже сконфигурированы при импорте."""
        return

else:
    # API и Django-команды: брокер после django.setup.
    # Import celery_app до setup обязателен (set_default), иначе amqp://localhost.
    # CLI: prepare_celery_for_django(); HTTP: asgi.py / wsgi.py.
    _django_celery_lock = threading.Lock()
    _django_celery_configured = False

    def ensure_django_celery_configured() -> None:
        """Брокер и маршруты для .delay() из API и Django-команд. После django.setup()."""
        global _django_celery_configured
        if _django_celery_configured:
            return
        with _django_celery_lock:
            if _django_celery_configured:
                return
            try:
                from django.conf import settings as django_settings
                from src.config.redis_runtime import (
                    ensure_kombu_redis_resp2,
                    uses_redis_celery_backend,
                )
                from src.core.utils.celery_config_cache import read_routes_queues_cache

                celery_app.config_from_object(django_settings, namespace='CELERY')
                broker = celery_app.conf.broker_url
                result = celery_app.conf.result_backend
                if broker and uses_redis_celery_backend(str(broker), str(result or '')):
                    ensure_kombu_redis_resp2()

                # HTTP-процесс: только прогретый bin, без скана celery_config модулей.
                cached = read_routes_queues_cache(validate_fingerprint=False)
                routes_source = 'cache'
                if cached is not None:
                    routes = cached['routes']
                    queues = cached['queues']
                else:
                    from src.core.utils.celery.manager import CeleryModuleManager

                    manager = CeleryModuleManager(use_config_cache=True)
                    routes = manager.get_all_task_routes()
                    queues = manager.get_all_task_queues()
                    routes_source = 'modules'

                if 'default' not in queues:
                    queues = dict(queues)
                    queues['default'] = {
                        'exchange': 'default',
                        'routing_key': 'default',
                    }
                celery_app.conf.update(
                    task_routes=routes,
                    task_default_queue='default',
                    task_queues=queues,
                )
                _django_celery_configured = True
                try:
                    from src.core.utils.request_id import get_request_id
                    from celery.signals import before_task_publish

                    @before_task_publish.connect(weak=False)
                    def _api_attach_request_id(headers=None, **_):
                        if headers is None:
                            return
                        token = get_request_id()
                        if token:
                            headers['ergo_request_id'] = token
                except Exception:
                    logger.debug('request_id publish signal skipped', exc_info=True)
                logger.info(
                    log_t(
                        'celery_django_broker_configured',
                        broker=celery_app.conf.broker_url,
                        routes_source=routes_source,
                        routes_count=len(routes),
                        queues_count=len(queues),
                    ),
                )
            except Exception as exc:
                logger.warning(log_t('celery_django_configure_failed', error=exc))

    _orig_send_task = celery_app.send_task

    def _send_task_with_django_broker(*args, **kwargs):
        # producer_or_acquire читает broker_url до before_task_publish.
        from django.conf import settings as django_settings

        if django_settings.configured:
            ensure_django_celery_configured()
        return _orig_send_task(*args, **kwargs)

    celery_app.send_task = _send_task_with_django_broker