"""
Менеджер для автоматического обнаружения и загрузки конфигураций Celery модулей.

Использует discovered_apps и файловый кэш routes/queues — при валидном кэше
импорт celery_config модулей не выполняется.
"""

import importlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from typing import Dict, Any, List, Optional, Tuple

from src.core.utils.celery.base import CeleryModuleConfig
from src.core.utils.celery.module_identity import resolve_celery_app_identity
from src.core.utils.auto_api.discovered_apps_cache import get_discovered_apps
from src.core.utils.celery_config_cache import read_routes_queues_cache, write_routes_queues_cache
from src.core.utils.module_registry import is_valid_module_name


class CeleryModuleManager:
    """
    Менеджер для управления конфигурациями Celery модулей.
    Загружает конфигурации модулей из кэшированного списка discovered_apps.
    """
    
    def __init__(self, use_config_cache: bool = True):
        self.modules_configs: Dict[str, CeleryModuleConfig] = {}
        self.logger = logging.getLogger('celery.manager')
        self._routes: Optional[Dict[str, Any]] = None
        self._queues: Optional[Dict[str, Any]] = None
        self._queue_limits: Optional[Dict[str, int]] = None
        self._annotations: Optional[Dict[str, Any]] = None
        self._additional_configs: Optional[Dict[str, Any]] = None
        self._module_names: Optional[List[str]] = None
        if use_config_cache:
            self._load_from_cache_or_modules()
        else:
            self._load_modules_from_cache()
    
    def _load_from_cache_or_modules(self) -> None:
        """Пробует загрузить routes/queues/limits из кэша, иначе — из модулей."""
        cached = read_routes_queues_cache(require_worker_fields=True)
        if cached is not None:
            self._routes = cached['routes']
            self._queues = cached['queues']
            self._queue_limits = cached.get('queue_limits') or {}
            self._annotations = cached.get('annotations') or {}
            self._additional_configs = cached.get('additional_configs') or {}
            self._module_names = list(cached.get('modules') or [])
            self.logger.debug('Celery: routes/queues загружены из кэша')
            return
        self._load_modules_from_cache()

    def _load_modules_from_cache(self) -> None:
        """Загружает конфигурации Celery для модулей из discovered_apps (параллельно)."""
        from src.core.utils.module_registry import get_disabled_modules
        disabled = get_disabled_modules()
        all_apps = get_discovered_apps()
        module_apps = [app for app in all_apps if app.startswith('modules.')]
        items: List[Tuple[str, str, str]] = []
        for app_path in module_apps:
            identity = resolve_celery_app_identity(app_path)
            if identity is None:
                continue
            config_key, catalog_name, logger_name = identity
            if catalog_name in disabled:
                continue
            if not is_valid_module_name(config_key):
                self.logger.warning(
                    f"Пропускаем модуль с невалидным именем: {config_key} (путь: {app_path})"
                )
                continue
            items.append((config_key, logger_name, app_path))
        max_workers = min(4, len(items) or 1)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(self._load_module_config, key, logger_name, path): key
                for key, logger_name, path in items
            }
            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    module_name, config_instance = result
                    self.modules_configs[module_name] = config_instance
        self._save_to_cache()

    def _load_module_config(
        self,
        config_key: str,
        logger_module_name: str,
        app_path: Optional[str] = None,
    ) -> Optional[Tuple[str, CeleryModuleConfig]]:
        """Загружает конфигурацию конкретного модуля"""
        config_module_path = (
            f'{app_path}.celery_config'
            if app_path
            else f'modules.{config_key}.celery_config'
        )

        try:
            self.logger.debug(
                f"Попытка загрузки конфигурации Celery для модуля {config_key} "
                f"по пути: {config_module_path}"
            )
            config_module = importlib.import_module(config_module_path)

            config_class = None
            for attr_name in dir(config_module):
                attr = getattr(config_module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, CeleryModuleConfig)
                    and attr != CeleryModuleConfig
                ):
                    config_class = attr
                    break

            if config_class:
                config_instance = config_class(logger_module_name)
                self.logger.debug(
                    f"Успешно загружена конфигурация Celery для модуля {config_key}"
                )
                return (config_key, config_instance)
            self.logger.debug(
                f"В модуле {config_module_path} не найден класс конфигурации, "
                f"используется дефолт"
            )
            return (
                config_key,
                self._create_default_config(logger_module_name, app_path),
            )
        except ImportError:
            self.logger.debug(
                f"Модуль {config_key} не имеет celery_config, создается дефолт"
            )
            return (
                config_key,
                self._create_default_config(logger_module_name, app_path),
            )
        except Exception as e:
            self.logger.error(
                f"Ошибка загрузки конфигурации модуля {config_key}: {e}",
                exc_info=True,
            )
            return (
                config_key,
                self._create_default_config(logger_module_name, app_path),
            )

    def _save_to_cache(self) -> None:
        """Сохраняет routes/queues и поля worker в кэш после загрузки модулей."""
        if self.modules_configs:
            write_routes_queues_cache(
                self.get_all_task_routes(),
                self.get_all_task_queues(),
                queue_limits=self.get_all_queue_limits(),
                annotations=self.get_all_task_annotations(),
                additional_configs=self.get_additional_configs(),
                modules=self.get_modules_list(),
            )

    def _create_default_config(self, module_name: str, app_path: Optional[str] = None) -> CeleryModuleConfig:
        """Создает базовую конфигурацию для модуля"""
        class DefaultModuleConfig(CeleryModuleConfig):
            def get_task_routes(self) -> Dict[str, Dict[str, Any]]:
                # Используем полный путь к модулю если он предоставлен
                module_path = app_path if app_path else f'modules.{module_name}'
                return {f'{module_path}.tasks.*': {'queue': module_name}}
            
            def get_task_queues(self) -> Dict[str, Dict[str, Any]]:
                return {
                    module_name: {
                        'exchange': module_name,
                        'routing_key': module_name,
                    }
                }
            
            def get_task_annotations(self) -> Dict[str, Dict[str, Any]]:
                return {}
        
        return DefaultModuleConfig(module_name)
    
    def get_all_task_routes(self) -> Dict[str, str]:
        """Собирает все маршруты задач (из кэша или модулей)."""
        if self._routes is not None:
            return self._routes
        routes = {}
        for config in self.modules_configs.values():
            routes.update(config.get_task_routes())
        return routes

    def get_all_task_queues(self) -> Dict[str, Dict[str, Any]]:
        """Собирает все очереди задач (из кэша или модулей)."""
        if self._queues is not None:
            return self._queues
        queues = {}
        for config in self.modules_configs.values():
            queues.update(config.get_task_queues())
        return queues
    
    def get_all_task_annotations(self) -> Dict[str, Dict[str, Any]]:
        """Собирает все аннотации задач из кэша или модулей."""
        if self._annotations is not None:
            return self._annotations
        annotations = {}
        for config in self.modules_configs.values():
            annotations.update(config.get_task_annotations())
        return annotations
    
    def get_module_loggers(self) -> Dict[str, Dict[str, logging.Logger]]:
        """Собирает все логгеры из всех модулей"""
        loggers = {}
        for module_name, config in self.modules_configs.items():
            loggers[module_name] = config.get_module_loggers()
        return loggers
    
    def get_additional_configs(self) -> Dict[str, Any]:
        """Собирает все дополнительные конфигурации из кэша или модулей."""
        if self._additional_configs is not None:
            return self._additional_configs
        configs = {}
        for config in self.modules_configs.values():
            configs.update(config.get_additional_config())
        return configs
    
    def get_modules_list(self) -> List[str]:
        """Возвращает список всех загруженных модулей"""
        if self._module_names is not None:
            return self._module_names
        return list(self.modules_configs.keys())
    
    def get_module_config(self, module_name: str) -> Optional[CeleryModuleConfig]:
        """Возвращает конфигурацию конкретного модуля."""
        return self.modules_configs.get(module_name)
    
    def get_all_queue_limits(self) -> Dict[str, int]:
        """Собирает лимиты параллелизма из кэша или модулей."""
        if self._queue_limits is not None:
            return self._queue_limits
        limits = {}
        for module_name, config in self.modules_configs.items():
            max_concurrent = config.get_max_concurrent_tasks()
            if max_concurrent > 0:
                queue_name = config.get_queue_name()
                limits[queue_name] = max_concurrent
                self.logger.debug(
                    f"Модуль {module_name}: очередь {queue_name}, "
                    f"max_concurrent_tasks={max_concurrent}"
                )
        return limits
    
    def setup_queue_concurrency(self):
        """
        Настраивает менеджер параллелизма очередей на основе конфигураций модулей.
        Должен вызываться после инициализации Celery.
        """
        from src.core.utils.celery.concurrency import queue_concurrency_manager
        
        from src.core.utils.celery.startup_format import format_limits_summary

        limits = self.get_all_queue_limits()
        for queue_name, max_concurrent in limits.items():
            queue_concurrency_manager.set_queue_limit(queue_name, max_concurrent)

        if limits:
            self.logger.info(
                "Celery: лимиты параллелизма (%d): %s",
                len(limits),
                format_limits_summary(limits),
            ) 