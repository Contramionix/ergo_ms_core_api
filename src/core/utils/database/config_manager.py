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

_YAML_CACHE: Dict[Path, tuple] = {}

# Глобальный кэш для уже обработанной Django-конфигурации БД внутри одного процесса.
# Это делает инициализацию БД/логгинга идемпотентной: даже при повторных импортах
# или создании нескольких DjangoDatabaseConfigLoader результат будет рассчитан один раз.
_DJANGO_DATABASES_CACHE: Optional[Dict] = None
_DJANGO_DATABASES_LOADED: bool = False


def _get_cached_yaml(config_path: Path) -> Optional[Dict]:
    """Читает databases.yaml с общим кэшем. Инвалидация по mtime."""
    try:
        resolved = config_path.resolve()
        mtime = resolved.stat().st_mtime
    except OSError:
        return None
    if resolved in _YAML_CACHE:
        cached_mtime, cached_config = _YAML_CACHE[resolved]
        if cached_mtime == mtime:
            return cached_config
    if not resolved.exists():
        logger.warning(f"Файл конфигурации не найден: {config_path}")
        return None
    try:
        with open(resolved, 'r', encoding='utf-8') as file:
            config = yaml.safe_load(file)
        if not config or 'databases' not in config:
            logger.error("Неверный формат файла конфигурации")
            return None
        _YAML_CACHE[resolved] = (mtime, config['databases'])
        logger.debug(f"Загружена конфигурация из {config_path}")
        return config['databases']
    except Exception as e:
        logger.error(f"Ошибка при чтении конфигурации: {e}")
        return None


def resolve_databases_yaml_path(system_dir: Path) -> Path:
    """
    Путь к databases.yaml.

    ERGO_DATABASES_YAML — абсолютный/относительный путь (loadtest ephemeral API).
    """
    override = (os.environ.get('ERGO_DATABASES_YAML') or '').strip()
    if override:
        return Path(override)
    return Path(system_dir) / 'databases.yaml'


# ==================== Константы ====================

DB_ENGINES = {
    'postgresql': 'django.db.backends.postgresql',
    'mysql': 'mysql.connector.django',
    'sqlite': 'django.db.backends.sqlite3',
    'mssql': 'django.db.backends.sqlserver',
}


# ==================== Базовый класс ====================

class BaseDatabaseConfigLoader(ABC):
    """
    Базовый абстрактный класс для загрузки конфигурации баз данных.
    Предоставляет общую логику работы с databases.yaml.
    """
    
    def __init__(self, system_dir: Path):
        """
        Инициализация загрузчика конфигурации.
        
        Args:
            system_dir: Путь к корневой директории системы
        """
        self.system_dir = system_dir
        self.config_path = resolve_databases_yaml_path(system_dir)
        self._raw_config: Optional[Dict] = None
        self._loaded = False
    
    def _load_yaml_config(self) -> Optional[Dict]:
        """Загружает YAML через общий кэш (один файл — одно чтение)."""
        return _get_cached_yaml(self.config_path)
    
    def load_config(self) -> Dict:
        """
        Загружает конфигурацию БД.
        
        Returns:
            Словарь с конфигурацией БД
        """
        if not self._loaded:
            self._raw_config = self._load_yaml_config()
            self._loaded = True
        
        return self._process_config(self._raw_config)
    
    @abstractmethod
    def _process_config(self, raw_config: Optional[Dict]) -> Dict:
        """
        Обрабатывает сырую конфигурацию и возвращает готовую к использованию.
        
        Args:
            raw_config: Сырая конфигурация из YAML
            
        Returns:
            Обработанная конфигурация
        """
        pass
    
    def get_section_config(self, section_name: str) -> Optional[Dict]:
        """
        Получает конфигурацию конкретной секции.
        
        Args:
            section_name: Имя секции (например, 'default', 'celery')
            
        Returns:
            Конфигурация секции или None
        """
        if self._raw_config is None:
            self._raw_config = self._load_yaml_config()
        
        if self._raw_config is None:
            return None
        
        return self._raw_config.get(section_name)
    
    def get_available_sections(self) -> List[str]:
        """
        Возвращает список доступных секций БД.
        
        Returns:
            Список имен секций
        """
        if self._raw_config is None:
            self._raw_config = self._load_yaml_config()
        
        if self._raw_config is None:
            return []
        
        return list(self._raw_config.keys())


# ==================== Класс для проверки подключения ====================

class DatabaseConnectionTester:
    """
    Класс для тестирования подключений к различным типам БД.
    """
    
    @staticmethod
    def test_postgresql(host: str, port: int, user: str, password: str, dbname: str) -> bool:
        """Тестирует подключение к PostgreSQL"""
        try:
            import psycopg2
            connection = psycopg2.connect(
                dbname=dbname,
                user=user,
                password=password,
                host=host,
                port=port,
                connect_timeout=5
            )
            connection.close()
            return True
        except Exception as e:
            # Извлекаем понятное сообщение об ошибке
            error_message = DatabaseConnectionTester._extract_postgresql_error(e, dbname)
            logger.error(f"PostgreSQL подключение не удалось: {error_message}")
            return False
    
    @staticmethod
    def _extract_postgresql_error(e: Exception, dbname: str = '') -> str:
        """
        Извлекает понятное сообщение об ошибке из исключения PostgreSQL.
        Правильно обрабатывает ошибки декодирования и разделяет их от ошибок подключения.
        
        Args:
            e: Исключение от psycopg2
            dbname: Имя базы данных (для более точных сообщений об ошибках)
        """
        # Сначала пытаемся извлечь понятное сообщение из args исключения
        messages = []
        decode_error_occurred = False
        
        for arg in e.args:
            if isinstance(arg, bytes):
                # Пытаемся декодировать байты
                try:
                    # Сначала пробуем UTF-8
                    decoded = arg.decode('utf-8', errors='strict')
                    messages.append(decoded)
                except UnicodeDecodeError:
                    decode_error_occurred = True
                    try:
                        # Пробуем cp1251 (Windows кодировка)
                        decoded = arg.decode('cp1251', errors='replace')
                        messages.append(decoded)
                    except Exception:
                        # Если не получилось - используем repr
                        messages.append(repr(arg))
            else:
                # Обычные строки
                try:
                    messages.append(str(arg))
                except Exception as decode_err:
                    # Если даже str() вызывает ошибку декодирования
                    decode_error_occurred = True
                    messages.append(f"[Ошибка декодирования: {type(decode_err).__name__}]")
        
        # Формируем итоговое сообщение
        if messages:
            base_message = ' '.join(messages).strip()
        else:
            base_message = str(type(e).__name__)
        
        # Определяем тип ошибки для более понятного сообщения
        error_type = type(e).__name__
        error_lower = base_message.lower()
        
        # Если произошла ошибка декодирования, добавляем пояснение
        if decode_error_occurred:
            # Пытаемся определить реальную причину ошибки подключения
            if 'password authentication failed' in error_lower or 'authentication failed' in error_lower:
                return "Ошибка авторизации: неверное имя пользователя или пароль"
            elif 'could not connect' in error_lower or 'connection refused' in error_lower:
                return "Не удалось подключиться к серверу: проверьте хост и порт"
            elif 'timeout' in error_lower:
                return "Превышено время ожидания подключения: сервер недоступен"
            elif 'database' in error_lower and 'does not exist' in error_lower:
                if dbname:
                    return f"База данных '{dbname}' не найдена"
                else:
                    return "База данных не найдена"
            else:
                return f"Ошибка подключения (также произошла ошибка декодирования сообщения): {base_message[:200]}"
        
        # Если ошибки декодирования не было, анализируем обычные ошибки
        if 'password authentication failed' in error_lower or 'authentication failed' in error_lower:
            return "Ошибка авторизации: неверное имя пользователя или пароль"
        elif 'could not connect' in error_lower or 'connection refused' in error_lower:
            return "Не удалось подключиться к серверу: проверьте хост и порт"
        elif 'timeout' in error_lower or 'timed out' in error_lower:
            return "Превышено время ожидания подключения: сервер недоступен"
        elif 'database' in error_lower and 'does not exist' in error_lower:
            if dbname:
                return f"База данных '{dbname}' не найдена"
            else:
                return "База данных не найдена"
        elif 'could not translate host name' in error_lower or 'getaddrinfo failed' in error_lower:
            return "Не удалось разрешить имя хоста: проверьте правильность адреса"
        else:
            # Возвращаем базовое сообщение, ограничив его длину
            return base_message[:500] if len(base_message) > 500 else base_message
    
    @staticmethod
    def test_mysql(host: str, port: int, user: str, password: str, database: str) -> bool:
        """Тестирует подключение к MySQL"""
        try:
            import mysql.connector
            connection = mysql.connector.connect(
                database=database,
                user=user,
                password=password,
                host=host,
                port=port,
                connection_timeout=5
            )
            connection.close()
            return True
        except Exception as e:
            logger.error(f"MySQL подключение не удалось: {str(e)}")
            return False
    
    @staticmethod
    def test_sqlite(db_path: str) -> bool:
        """Тестирует подключение к SQLite"""
        try:
            import sqlite3
            connection = sqlite3.connect(db_path, timeout=5)
            connection.close()
            return True
        except Exception as e:
            logger.error(f"SQLite подключение не удалось: {str(e)}")
            return False
    
    @staticmethod
    def test_mssql(host: str, port: int, user: str, password: str, database: str) -> bool:
        """Тестирует подключение к MS SQL Server"""
        try:
            import pyodbc
            connection_string = (
                f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                f"SERVER={host},{port};"
                f"DATABASE={database};"
                f"UID={user};"
                f"PWD={password};"
                f"Connection Timeout=5;"
            )
            connection = pyodbc.connect(connection_string)
            connection.close()
            return True
        except Exception as e:
            logger.error(f"MSSQL подключение не удалось: {str(e)}")
            return False
    
    @classmethod
    def test_connection(cls, engine: str, config: Dict) -> bool:
        """
        Тестирует подключение к БД на основе типа движка.
        
        Args:
            engine: Тип БД ('postgresql', 'mysql', 'sqlite', 'mssql')
            config: Конфигурация подключения
            
        Returns:
            True если подключение успешно, иначе False
        """
        try:
            if engine == 'postgresql':
                return cls.test_postgresql(
                    config['host'], config['port'],
                    config['user'], config['password'],
                    config['name']
                )
            elif engine == 'mysql':
                return cls.test_mysql(
                    config['host'], config['port'],
                    config['user'], config['password'],
                    config['name']
                )
            elif engine == 'sqlite':
                return cls.test_sqlite(config['name'])
            elif engine == 'mssql':
                return cls.test_mssql(
                    config['host'], config['port'],
                    config['user'], config['password'],
                    config['name']
                )
            else:
                logger.error(f"Неизвестный тип БД: {engine}")
                return False
        except Exception as e:
            logger.error(f"Ошибка при тестировании подключения: {str(e)}")
            return False


# Re-export loaders (split for file size limit; public API unchanged).
from .config_manager_loaders import (  # noqa: E402
    CeleryDatabaseConfigLoader,
    DjangoDatabaseConfigLoader,
)

__all__ = [
    'BaseDatabaseConfigLoader',
    'DatabaseConnectionTester',
    'DjangoDatabaseConfigLoader',
    'CeleryDatabaseConfigLoader',
    'DB_ENGINES',
    'resolve_databases_yaml_path',
]
