"""
Файл с функциями для автоматизации сборки Django приложений (модулей).
"""

import os
import re
import importlib
import inspect
import logging
from pathlib import Path

from typing import List, Dict, Optional, Tuple

from django.apps import AppConfig
from django.urls import (
    include,
    path,
)

from src.config.env import env
from src.config.settings.base import MODULES_DIR, SYSTEM_DIR

logger = logging.getLogger('utils')

class ModuleDiscoverer:
    """
    Единый класс для обнаружения модулей системы (core и внешних).
    Унифицирует подход к поиску модулей, аналогично ModuleLoader в клиенте.
    """
    
    def __init__(self):
        self._cache = {}

    def discover_client_route_modules(self) -> Dict[str, str]:
        """
        Находит модули, у которых есть client-файл маршрутов routes.js.

        Источники:
        - внешние модули: modules/<module_name>/client/js/routes.js
        - внутренние модули ядра: core/client/src/core/<module_name>/js/routes.js

        Returns:
            Dict[str, str]: словарь {module_key: absolute_routes_path}
                            где module_key:
                              - 'module:<name>' для внешних модулей
                              - 'core:<name>'   для внутренних core-модулей
        """
        from src.core.utils.module_registry import (
            get_disabled_modules,
            is_valid_module_dir_name,
        )
        disabled = get_disabled_modules()
        result: Dict[str, str] = {}

        # 1. Внешние модули:
        #    - modules/<module_name>/client/js/routes.js
        #    - modules/<module_name>/client/<section>/js/routes.js (вложенные подприложения)
        # Клиентский discovery — все non-disabled (не фильтр HTTP-процесса).
        if MODULES_DIR.exists() and MODULES_DIR.is_dir():
            for module_dir in MODULES_DIR.iterdir():
                if not module_dir.is_dir() or not is_valid_module_dir_name(module_dir.name):
                    continue
                if module_dir.name in disabled:
                    continue

                routes_path = module_dir / 'client' / 'js' / 'routes.js'
                if routes_path.exists():
                    result[f'module:{module_dir.name}'] = str(routes_path)

                nested_root = module_dir / 'client'
                if nested_root.is_dir():
                    for nested_dir in nested_root.iterdir():
                        if not nested_dir.is_dir() or nested_dir.name == 'js':
                            continue
                        nested_routes = nested_dir / 'js' / 'routes.js'
                        if nested_routes.exists():
                            result[f'module:{module_dir.name}:{nested_dir.name}'] = str(
                                nested_routes
                            )

        # 2. Внутренние модули ядра (core/client/src/core/<module_name>/js/routes.js)
        core_client_root = SYSTEM_DIR / 'core' / 'client' / 'src' / 'core'
        if core_client_root.exists() and core_client_root.is_dir():
            for core_module_dir in core_client_root.iterdir():
                if not core_module_dir.is_dir():
                    continue

                routes_path = core_module_dir / 'js' / 'routes.js'
                if routes_path.exists():
                    key = f'core:{core_module_dir.name}'
                    result[key] = str(routes_path)

        return result

    def _recursively_find_apps(self, current_dir: str, base_module: str, installed_apps: List[str]) -> None:
        """
        Рекурсивно находит приложения в core директории.
        
        Аргументы:
            current_dir (str): Текущая директория для обхода.
            base_module (str): Базовый модуль для текущей директории.
            installed_apps (list): Список для добавления найденных приложений.
        """
        if not os.path.isdir(current_dir):
            return
        
        for app_name in os.listdir(current_dir):
            app_path = os.path.join(current_dir, app_name)
            
            if os.path.isdir(app_path):
                module_path = f'{base_module}.{app_name}' if base_module else app_name
                
                # Проверяем наличие файла apps.py
                apps_py_path = os.path.join(app_path, 'apps.py')
                if os.path.exists(apps_py_path):
                    try:
                        # Пытаемся импортировать модуль apps
                        app_module = importlib.import_module(f'{module_path}.apps')
                        
                        # Ищем класс AppConfig
                        app_config = None
                        for name, obj in inspect.getmembers(app_module, inspect.isclass):
                            if issubclass(obj, AppConfig) and obj is not AppConfig:
                                app_config = obj
                                break
                        
                        if app_config:
                            installed_apps.append(module_path)
                            logger.debug(f"Найдено приложение: {module_path}")
                    except ModuleNotFoundError:
                        logger.error("Модуль не найден: %s.apps", module_path)
                    except AttributeError:
                        logger.error("Ошибка атрибута: %s.apps не имеет допустимого класса AppConfig", module_path)
                
                # Продолжаем рекурсивный обход независимо от наличия apps.py
                self._recursively_find_apps(app_path, module_path, installed_apps)
    
    def _find_modules_apps(self, modules_dir: str, installed_apps: List[str]) -> None:
        """
        Находит приложения во внешних модулях (структура modules/<module_name>/api/...).
        
        Аргументы:
            modules_dir (str): Директория модулей.
            installed_apps (list): Список для добавления найденных приложений.
        """
        if not os.path.isdir(modules_dir):
            return
        
        from src.core.utils.module_registry import (
            is_module_loadable_in_process,
            is_valid_module_dir_name,
        )

        for module_name in os.listdir(modules_dir):
            if not is_valid_module_dir_name(module_name):
                continue
            if not is_module_loadable_in_process(module_name):
                continue
            module_path = os.path.join(modules_dir, module_name)
            
            if os.path.isdir(module_path):
                # Проверяем наличие папки api
                api_path = os.path.join(module_path, 'api')
                if os.path.isdir(api_path):
                    # Формируем базовый префикс для модуля
                    base_module = f'modules.{module_name}.api'
                    
                    # Рекурсивно ищем приложения в папке api
                    self._find_apps_in_api(api_path, base_module, installed_apps)
    
    def _find_apps_in_api(self, current_dir: str, current_module: str, installed_apps: List[str]) -> None:
        """
        Рекурсивно находит приложения в папке api модуля.
        
        Аргументы:
            current_dir (str): Текущая директория для обхода.
            current_module (str): Текущий модуль.
            installed_apps (list): Список для добавления найденных приложений.
        """
        # Сначала проверяем apps.py на текущем уровне
        apps_py_path = os.path.join(current_dir, 'apps.py')
        if os.path.exists(apps_py_path):
            try:
                # Пытаемся импортировать модуль apps
                import_path = f'{current_module}.apps'
                app_module = importlib.import_module(import_path)
                
                # Ищем класс AppConfig
                app_config = None
                for name, obj in inspect.getmembers(app_module, inspect.isclass):
                    if issubclass(obj, AppConfig) and obj is not AppConfig:
                        app_config = obj
                        break
                
                if app_config:
                    installed_apps.append(current_module)
                    logger.debug(f"Найдено приложение модуля: {current_module}")
            except ModuleNotFoundError:
                logger.error("Модуль не найден: %s.apps", current_module)
            except AttributeError:
                logger.error("Ошибка атрибута: %s.apps не имеет допустимого класса AppConfig", current_module)
        
        # Затем ищем вложенные приложения
        if not os.path.isdir(current_dir):
            return
        
        for app_name in os.listdir(current_dir):
            app_path = os.path.join(current_dir, app_name)
            
            if os.path.isdir(app_path) and app_name != '__pycache__' and not app_name.startswith('.'):
                # Формируем путь к вложенному модулю
                nested_module = f'{current_module}.{app_name}'
                # Рекурсивно проверяем вложенную папку
                self._find_apps_in_api(app_path, nested_module, installed_apps)
    
    def _recursively_find_urls(self, current_dir: str, current_prefix: str, current_route: str, urlpatterns: List) -> None:
        """
        Рекурсивно находит URL конфигурации в core директории.
        
        Аргументы:
            current_dir (str): Текущая директория для обхода.
            current_prefix (str): Текущий префикс для импорта модулей.
            current_route (str): Текущий маршрут, учитывающий иерархию модулей.
            urlpatterns (list): Список для добавления найденных URL паттернов.
        """
        if not os.path.isdir(current_dir):
            return
        
        for module_name in os.listdir(current_dir):
            if module_name.startswith('.') or module_name in (
                '__pycache__',
                'migrations',
                'node_modules',
                'dist',
            ):
                continue
            module_path = os.path.join(current_dir, module_name)
            
            if os.path.isdir(module_path):
                # Проверяем, является ли папка Python-пакетом (имеет __init__.py)
                init_py_path = os.path.join(module_path, '__init__.py')
                if os.path.exists(init_py_path):
                    # Формируем полный путь к модулю
                    module_full_path = f"{current_prefix}.{module_name}" if current_prefix else module_name
                    
                    # Формируем маршрут с учетом иерархии
                    new_route = f"{current_route}{module_name}/"
                    
                    # Проверяем наличие файла urls.py
                    urls_py_path = os.path.join(module_path, 'urls.py')
                    if os.path.exists(urls_py_path):
                        # Формируем маршрут и добавляем его в urlpatterns
                        url_pattern = path(new_route, include(f"{module_full_path}.urls"))
                        urlpatterns.append(url_pattern)
                    
                    # Рекурсивно обходим подмодули
                    self._recursively_find_urls(module_path, module_full_path, new_route, urlpatterns)
    
    def _find_modules_urls(self, modules_dir: str, urlpatterns: List) -> None:
        """
        Находит URL конфигурации во внешних модулях (структура modules/<module_name>/api/...).
        
        Аргументы:
            modules_dir (str): Директория модулей.
            urlpatterns (list): Список для добавления найденных URL паттернов.
        """
        from src.core.utils.django_cli import is_lean_schema_cli

        # migrate/makemigrations: module ready() пропущен, bridge пуст — URL модулей не нужны
        if is_lean_schema_cli():
            logger.debug('Lean schema CLI: пропуск discovery URL модулей')
            return

        if not os.path.isdir(modules_dir):
            return
        
        from src.core.utils.module_registry import (
            is_module_loadable_in_process,
            is_valid_module_dir_name,
        )

        for module_name in os.listdir(modules_dir):
            if not is_valid_module_dir_name(module_name):
                continue
            if not is_module_loadable_in_process(module_name):
                continue
            module_path = os.path.join(modules_dir, module_name)
            
            if os.path.isdir(module_path):
                # Проверяем наличие папки api
                api_path = os.path.join(module_path, 'api')
                if os.path.isdir(api_path):
                    # Формируем базовый префикс для модуля
                    base_module = f'modules.{module_name}.api'
                    
                    # Рекурсивно ищем urls.py в папке api
                    self._find_urls_in_api(api_path, base_module, urlpatterns)
    
    def _find_urls_in_api(self, current_dir: str, current_module: str, urlpatterns: List) -> None:
        """
        Рекурсивно находит URL конфигурации в папке api модуля.
        
        Аргументы:
            current_dir (str): Текущая директория для обхода.
            current_module (str): Текущий модуль.
            urlpatterns (list): Список для добавления найденных URL паттернов.
        """
        from django.conf import settings
        installed_apps = getattr(settings, 'INSTALLED_APPS', [])
        
        app_base = current_module
        if app_base not in installed_apps:
            parts = current_module.split('.')
            if len(parts) >= 3:
                app_base = '.'.join(parts[:3])
        
        if app_base not in installed_apps:
            return
        
        urls_py_path = os.path.join(current_dir, 'urls.py')
        if os.path.exists(urls_py_path):
            try:
                route_parts = current_module.replace('modules.', '').replace('.api', '').split('.')
                route = '/'.join(route_parts) + '/'
                
                logger.debug(f"Найден файл urls.py в модуле: {current_module}, маршрут: {route}")
                url_pattern = path(route, include(f"{current_module}.urls"))
                urlpatterns.append(url_pattern)
            except Exception as e:
                logger.error("Ошибка при добавлении URL для модуля %s: %s", current_module, e)
        
        # Затем ищем вложенные urls.py
        if not os.path.isdir(current_dir):
            return
        
        for item_name in os.listdir(current_dir):
            item_path = os.path.join(current_dir, item_name)
            
            if os.path.isdir(item_path) and item_name != '__pycache__' and not item_name.startswith('.'):
                # Формируем путь к вложенному модулю
                nested_module = f'{current_module}.{item_name}'
                # Рекурсивно проверяем вложенную папку
                self._find_urls_in_api(item_path, nested_module, urlpatterns)
    
    def clear_cache(self):
        """Очищает весь кеш."""
        self._cache.clear()

def check_app_config_name(directory: str, config_name: str) -> bool:
    """
    Проверяет все файлы apps.py в указанной директории на наличие определенного названия конфига.

    Аргументы:
        directory (str): Директория, в которой находятся файлы apps.py.
        config_name (str): Название конфига, которое нужно проверить.

    Возвращает:
        bool: True, если конфиг найден, иначе False.
    """
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file == 'apps.py':
                file_path = os.path.join(root, file)

                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                    searched_class_signature = rf'class\s+{config_name}Config\s*\(AppConfig\):'
                    if re.search(searched_class_signature, content):
                        return True
    return False

def get_env_deploy_type():
    from src.config.deploy import get_settings_module

    return get_settings_module()
    
def is_valid_module_name(module_name: str) -> bool:
    """Проверка имени модуля; реализация — в module_registry (без Django)."""
    from src.core.utils.module_registry import is_valid_module_name as _is_valid

    return _is_valid(module_name)
