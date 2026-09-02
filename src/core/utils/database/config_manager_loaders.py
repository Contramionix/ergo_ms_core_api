"""Django/Celery loaders for database config."""

"""
Централизованная объектно-ориентированная система управления конфигурацией баз данных.
Использует паттерн Strategy и наследование для гибкой работы с разными типами БД.
"""

import os
import yaml
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from urllib.parse import quote_plus

from src.config.env import env

logger = logging.getLogger('utils.database.config')

from .config_manager import (
    BaseDatabaseConfigLoader,
    DatabaseConnectionTester,
    DB_ENGINES,
    _DJANGO_DATABASES_CACHE,
    _DJANGO_DATABASES_LOADED,
    _get_cached_yaml,
)

class DjangoDatabaseConfigLoader(BaseDatabaseConfigLoader):
    """
    Загрузчик конфигурации для Django DATABASES.
    Преобразует YAML конфигурацию в формат Django.
    """
    
    def __init__(self, system_dir: Path, resources_dir: Path, test_connections: bool = True):
        """
        Args:
            system_dir: Путь к корневой директории системы
            resources_dir: Путь к директории ресурсов
            test_connections: Тестировать ли подключения при загрузке
        """
        super().__init__(system_dir)
        self.resources_dir = resources_dir
        self.test_connections = test_connections
        self.connection_tester = DatabaseConnectionTester()
    
    def load_config(self) -> Dict:
        """
        Загружает конфигурацию БД в формате Django, с глобальным кэшем на процесс.

        Это предотвращает повторную обработку YAML и повторные тесты подключений
        при многократном импорте настроек или создании нескольких загрузчиков.
        """
        global _DJANGO_DATABASES_CACHE, _DJANGO_DATABASES_LOADED

        if _DJANGO_DATABASES_LOADED and _DJANGO_DATABASES_CACHE is not None:
            return _DJANGO_DATABASES_CACHE

        if not self._loaded:
            self._raw_config = self._load_yaml_config()
            self._loaded = True

        databases = self._process_config(self._raw_config)
        _DJANGO_DATABASES_CACHE = databases
        _DJANGO_DATABASES_LOADED = True
        return databases

    def _build_django_config(self, db_name: str, db_config: Dict) -> Dict:
        """
        Строит конфигурацию БД в формате Django.
        
        Args:
            db_name: Имя БД
            db_config: Сырая конфигурация из YAML
            
        Returns:
            Конфигурация в формате Django
        """
        engine = db_config.get('engine', 'postgresql').lower()
        
        if engine not in DB_ENGINES:
            logger.error(f"Неподдерживаемый тип СУБД: {engine}")
            raise ValueError(f"Неподдерживаемый тип СУБД: {engine}")
        
        django_config = {
            'ENGINE': DB_ENGINES[engine],
            'NAME': db_config['name'],
        }
        
        # SQLite требует только путь к файлу
        if engine != 'sqlite':
            django_config.update({
                'USER': db_config['user'],
                'PASSWORD': db_config['password'],
                'HOST': db_config['host'],
                'PORT': db_config['port'],
            })
            
            # SSH туннель
            if 'ssh' in db_config:
                logger.info(f"Настройка SSH туннеля для БД '{db_name}'")
                django_config['SSH'] = {
                    'host': db_config['ssh'].get('host'),
                    'port': db_config['ssh'].get('port', 22),
                    'username': db_config['ssh'].get('username'),
                    'password': db_config['ssh'].get('password'),
                    'key_path': db_config['ssh'].get('key_path'),
                    'remote_host': db_config['host'],
                    'remote_port': db_config['port'],
                }
            
            # Специфичные настройки для СУБД
            if engine == 'mysql':
                django_config['OPTIONS'] = {
                    'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
                    'charset': 'utf8mb4',
                }
            elif engine == 'mssql':
                django_config['OPTIONS'] = {
                    'driver': 'ODBC Driver 17 for SQL Server',
                    'unicode_results': True,
                }

            conn_max_age = env.int('API_DATABASE_CONN_MAX_AGE', default=60)
            if conn_max_age > 0:
                django_config['CONN_MAX_AGE'] = conn_max_age

        module_owner = str(db_config.get('module') or '').strip()
        if module_owner:
            django_config['MODULE'] = module_owner
        
        return django_config
    
    def _get_fallback_sqlite_config(self) -> Dict:
        """Возвращает fallback конфигурацию SQLite"""
        return {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': str(self.resources_dir / 'db.sqlite3'),
        }
    
    def _process_config(self, raw_config: Optional[Dict]) -> Dict:
        """
        Обрабатывает конфигурацию и преобразует в формат Django.
        
        Args:
            raw_config: Сырая конфигурация из YAML
            
        Returns:
            Конфигурация в формате Django DATABASES
        """
        if raw_config is None:
            logger.warning("Конфигурация не загружена, используется SQLite по умолчанию")
            return {'default': self._get_fallback_sqlite_config()}
        
        databases = {}
        
        for db_name, db_config in raw_config.items():
            # redis — не Django DATABASES; параметры читает redis_runtime
            if db_name == 'redis' or str(db_config.get('engine', '')).lower() == 'redis':
                continue
            try:
                effective_config = dict(db_config)
                if db_name == 'default':
                    from src.config.ergo_runtime import apply_default_db_engine

                    effective_config = apply_default_db_engine(effective_config)
                    if effective_config.get('engine', '').lower() == 'sqlite':
                        name = str(effective_config.get('name') or '').strip()
                        if not name or name == 'ergo_ms':
                            effective_config['name'] = str(
                                self.resources_dir / 'db.sqlite3'
                            )

                django_config = self._build_django_config(db_name, effective_config)

                if self.test_connections:
                    engine = effective_config.get('engine', 'postgresql').lower()
                    if self.connection_tester.test_connection(engine, effective_config):
                        logger.debug(
                            "Успешное подключение к БД '%s' (тип: %s)",
                            db_name,
                            DB_ENGINES[engine],
                        )
                        databases[db_name] = django_config
                    else:
                        logger.error("Не удалось подключиться к БД '%s'", db_name)
                        if db_name == 'default':
                            databases[db_name] = self._get_fallback_sqlite_config()
                            logger.warning("БД '%s' переключена на SQLite", db_name)
                else:
                    databases[db_name] = django_config

            except Exception as e:
                logger.error("Ошибка при обработке БД '%s': %s", db_name, e)
                if db_name == 'default':
                    databases[db_name] = self._get_fallback_sqlite_config()

        if 'default' not in databases:
            logger.warning('Создано подключение SQLite по умолчанию')
            databases['default'] = self._get_fallback_sqlite_config()

        return databases


# ==================== Класс для Celery БД ====================

class CeleryDatabaseConfigLoader(BaseDatabaseConfigLoader):
    """
    Загрузчик конфигурации для Celery (broker и result backend).
    Поддерживает приоритеты секций и локальный режим.
    """
    
    def __init__(self, system_dir: Path, virtual_env_dir: Path, 
                 section_priorities: List[str], component_name: str = "Celery"):
        """
        Args:
            system_dir: Путь к корневой директории системы
            virtual_env_dir: Путь к виртуальному окружению
            section_priorities: Список секций в порядке приоритета (например, ['celery_worker', 'celery'])
            component_name: Имя компонента для логирования (например, "Celery Worker")
        """
        super().__init__(system_dir)
        self.virtual_env_dir = virtual_env_dir
        self.section_priorities = section_priorities
        self.component_name = component_name
        self.active_section: Optional[str] = None
        self.active_config: Optional[Dict] = None
    
    def _celery_broker_backend(self) -> str:
        explicit = os.environ.get('CELERY_BROKER_BACKEND', '').strip().lower()
        if explicit:
            return explicit
        from src.config.redis_runtime import effective_celery_broker_backend

        return effective_celery_broker_backend()

    def _check_use_local_mode(self) -> bool:
        """Локальный SQLite: CELERY_BROKER_BACKEND=local или ERGO_BROKER=local без database."""
        if self._celery_broker_backend() == 'local':
            logger.info(
                '%s: локальный SQLite по CELERY_BROKER_BACKEND/ERGO_BROKER=local',
                self.component_name,
            )
            return True
        use_local = os.environ.get('CELERY_USE_LOCAL', 'false').lower() == 'true'
        if use_local:
            logger.info('%s: локальный режим по CELERY_USE_LOCAL', self.component_name)
        return use_local

    def _check_use_redis_mode(self) -> bool:
        """Redis-брокер: CELERY_BROKER_BACKEND=redis или ERGO_BROKER/REDIS_ENABLED."""
        backend = self._celery_broker_backend()
        if backend == 'redis':
            return True
        if backend in ('database', 'local'):
            return False
        if backend != 'auto':
            logger.warning(
                '%s: неизвестный CELERY_BROKER_BACKEND=%r, используется auto',
                self.component_name,
                backend,
            )
        from src.config.redis_runtime import redis_enabled

        if redis_enabled():
            logger.info(
                '%s: Redis-брокер по ERGO_BROKER/REDIS_ENABLED (CELERY_BROKER_BACKEND=auto)',
                self.component_name,
            )
        return redis_enabled()

    def _get_redis_urls(self) -> Tuple[str, str]:
        from src.config.redis_runtime import celery_broker_redis_url, celery_result_redis_url

        broker_url = celery_broker_redis_url()
        result_backend = celery_result_redis_url()
        logger.info(
            "%s: брокер Redis (%s), results (%s)",
            self.component_name,
            broker_url.split('?')[0],
            result_backend.split('?')[0],
        )
        return broker_url, result_backend
    
    def _find_active_section(self) -> Optional[str]:
        """
        Находит активную секцию по приоритетам.

        Returns:
            Имя активной секции или None
        """
        available = self.get_available_sections()

        for section in self.section_priorities:
            if section in available:
                logger.debug(f"{self.component_name}: Используется секция '{section}'")
                return section

        return None
    
    def _get_local_sqlite_urls(self, *, log_reason: Optional[str] = None) -> Tuple[str, str]:
        """
        Возвращает URLs для локального SQLite.
        log_reason: если задан, логирует одну строку вместо двух ('не найдена' + 'локальный SQLite').
        """
        celery_dir = self.virtual_env_dir / 'celery'
        celery_dir.mkdir(parents=True, exist_ok=True)
        
        broker_url = f'sqla+sqlite:///{self.virtual_env_dir}/celery/celerydb.sqlite'
        result_backend = f'db+sqlite:///{self.virtual_env_dir}/celery/results.sqlite'
        
        if log_reason:
            logger.info(f"{self.component_name}: {log_reason}, используется локальный SQLite")
        else:
            logger.info(f"{self.component_name}: Используется локальный SQLite")
        return broker_url, result_backend
    
    def _build_celery_urls(self, db_config: Dict) -> Tuple[str, str]:
        """
        Строит URLs для Celery broker и result backend.
        
        Args:
            db_config: Конфигурация БД
            
        Returns:
            Tuple (broker_url, result_backend)
        """
        engine = db_config.get('engine', 'postgresql').lower()
        
        # Проверяем обязательные поля
        host = db_config.get('host', '').strip()
        if not host:
            logger.warning(
                f"{self.component_name}: Host не указан в конфигурации БД, "
                "используется локальный SQLite"
            )
            return self._get_local_sqlite_urls()
        
        # Экранируем credentials
        user = quote_plus(db_config.get('user', ''))
        password = quote_plus(db_config.get('password', ''))
        port = db_config.get('port', 5432 if engine == 'postgresql' else 3306)
        name = db_config.get('name', '')
        
        if not name:
            logger.warning(
                f"{self.component_name}: Имя БД не указано в конфигурации, "
                "используется локальный SQLite"
            )
            return self._get_local_sqlite_urls()
        
        if engine == 'postgresql':
            broker_url = f"sqla+postgresql://{user}:{password}@{host}:{port}/{name}"
            result_backend = f"db+postgresql://{user}:{password}@{host}:{port}/{name}"
        elif engine == 'mysql':
            broker_url = f"sqla+mysql://{user}:{password}@{host}:{port}/{name}"
            result_backend = f"db+mysql://{user}:{password}@{host}:{port}/{name}"
        elif engine == 'sqlite':
            db_path = name
            broker_url = f'sqla+sqlite:///{db_path}'
            result_backend = f'db+sqlite:///{db_path}'
        else:
            logger.warning(f"{self.component_name}: Неподдерживаемый тип БД '{engine}'")
            return self._get_local_sqlite_urls()
        
        logger.debug(f"{self.component_name}: Настроена работа с {engine} БД ({host}:{port}/{name})")
        return broker_url, result_backend
    
    def _process_config(self, raw_config: Optional[Dict]) -> Dict:
        """
        Обрабатывает конфигурацию и возвращает URLs для Celery.
        
        Returns:
            Dict с broker_url, result_backend и metadata
        """
        # Проверяем локальный режим
        if self._check_use_local_mode():
            broker_url, result_backend = self._get_local_sqlite_urls()
            return {
                'broker_url': broker_url,
                'result_backend': result_backend,
                'mode': 'local',
                'section': None,
            }

        backend = self._celery_broker_backend()
        force_database = backend == 'database'
        force_redis = backend == 'redis'

        # ERGO_BROKER/REDIS / CELERY_BROKER_BACKEND=redis — выше секций celery в yaml
        if force_redis or (backend == 'auto' and self._check_use_redis_mode()):
            broker_url, result_backend = self._get_redis_urls()
            return {
                'broker_url': broker_url,
                'result_backend': result_backend,
                'mode': 'redis',
                'section': None,
            }

        # SQL-брокер из databases.yaml (celery* секции)
        if raw_config is not None and not force_redis:
            self.active_section = self._find_active_section()
            if self.active_section is not None or force_database:
                if self.active_section is None:
                    priorities = self.section_priorities
                    broker_url, result_backend = self._get_local_sqlite_urls(
                        log_reason=(
                            f'CELERY_BROKER_BACKEND=database, но секции {priorities} не найдены'
                        ),
                    )
                    return {
                        'broker_url': broker_url,
                        'result_backend': result_backend,
                        'mode': 'local',
                        'section': None,
                    }

                self.active_config = raw_config[self.active_section]
                if self.active_config is None:
                    broker_url, result_backend = self._get_local_sqlite_urls()
                    return {
                        'broker_url': broker_url,
                        'result_backend': result_backend,
                        'mode': 'local',
                        'section': None,
                    }

                broker_url, result_backend = self._build_celery_urls(self.active_config)
                return {
                    'broker_url': broker_url,
                    'result_backend': result_backend,
                    'mode': 'database',
                    'section': self.active_section,
                    'engine': self.active_config.get('engine', 'postgresql'),
                }
        
        # Проверяем доступность конфигурации
        if raw_config is None:
            broker_url, result_backend = self._get_local_sqlite_urls()
            return {
                'broker_url': broker_url,
                'result_backend': result_backend,
                'mode': 'local',
                'section': None,
            }

        priorities = self.section_priorities
        broker_url, result_backend = self._get_local_sqlite_urls(
            log_reason=f"Ни одна из секций {priorities} не найдена"
        )
        return {
            'broker_url': broker_url,
            'result_backend': result_backend,
            'mode': 'local',
            'section': None,
        }
    
    def get_active_section(self) -> Optional[str]:
        """
        Возвращает имя активной секции БД.
        
        Returns:
            Имя секции или None, если используется локальный режим
        """
        if self.active_section is None:
            # Если секция еще не определена, загружаем конфигурацию
            self.load_config()
        return self.active_section
    
    def get_django_db_alias(self) -> Optional[str]:
        """
        Возвращает alias Django БД для django-celery-beat.
        Использует БД, указанную в конфиге согласно приоритетам секций.
        Для celery beat это может быть: celery_beat -> celery -> локальный SQLite
        
        Returns:
            Alias БД или None для локального режима
        """
        config = self.load_config()
        
        if config['mode'] in ('local', 'redis'):
            return None
        
        # Возвращаем секцию из конфига (celery_beat, celery и т.д.)
        # Система автоматически выбирает правильную БД по приоритетам
        return config['section']

