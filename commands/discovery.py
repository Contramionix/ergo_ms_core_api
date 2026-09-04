"""
Модуль для автоматического обнаружения Django команд.

Discovery выполняется без загрузки Django — по файловой системе и кэшу
discovered_apps. Django загружается только при выполнении команды.
"""

import os
import sys

from pathlib import Path
from typing import Dict, List, Optional, Type

from commands.base import PoetryCommand

# Список встроенных команд Django (без загрузки Django)
DJANGO_BUILTIN_COMMANDS = frozenset([
    'changepassword', 'check', 'clearsessions', 'collectstatic', 'compilemessages',
    'createcachetable', 'createsuperuser', 'dbshell', 'diffsettings', 'dumpdata',
    'flush', 'inspectdb', 'loaddata', 'makemessages', 'makemigrations', 'migrate',
    'runserver', 'sendtestemail', 'shell', 'showmigrations', 'sqlflush', 'sqlmigrate',
    'sqlsequencereset', 'squashmigrations', 'startapp', 'startproject', 'test', 'testserver',
])


def _ensure_path() -> None:
    """Добавляет api/ в sys.path для импорта src (без Django)."""
    commands_dir = os.path.dirname(os.path.abspath(__file__))
    api_dir = os.path.join(commands_dir, '..')
    api_path = os.path.abspath(api_dir)
    if api_path not in sys.path:
        sys.path.insert(0, api_path)


def _module_path_to_fs_path(app_module: str, core_dir: Path, modules_dir: Path) -> Optional[Path]:
    """Преобразует путь модуля приложения в путь в файловой системе."""
    if app_module.startswith('src.core.'):
        rel_parts = app_module.replace('src.core.', '').split('.')
        path = core_dir.joinpath(*rel_parts)
        return path if path.exists() else None
    if app_module.startswith('modules.'):
        parts = app_module.split('.')
        if len(parts) < 3:
            return None
        module_name = parts[1]
        rest = parts[3:] if len(parts) > 3 else []
        path = modules_dir / module_name / 'api'
        for p in rest:
            path = path / p
        return path if path.exists() else None
    return None


def _get_app_commands_from_fs(core_dir: Path, modules_dir: Path) -> List[str]:
    """Сканирует приложения и собирает имена команд из management/commands/."""
    commands: List[str] = []
    seen: set = set()

    try:
        from src.core.utils.auto_api.discovered_apps_cache import get_discovered_apps
    except ImportError:
        return []

    for app_module in get_discovered_apps():
        app_path = _module_path_to_fs_path(app_module, core_dir, modules_dir)
        if not app_path or not app_path.is_dir():
            continue
        commands_dir = app_path / 'management' / 'commands'
        if not commands_dir.exists():
            continue
        for f in commands_dir.glob('*.py'):
            if f.name == '__init__.py':
                continue
            if f.name.startswith('_') or f.name.endswith('_'):
                continue
            # Вспомогательные модули рядом с командами (не management-команды)
            if f.stem.endswith('_lib'):
                continue
            name = f.stem
            if name in ['__init__', '__pycache__']:
                continue
            if name not in seen:
                seen.add(name)
                commands.append(name)
    return commands


def _discover_commands_fast() -> Dict[str, str]:
    """Discovery без Django: статический список + сканирование файлов."""
    _ensure_path()
    try:
        from src.config.settings.base import DJANGO_CORE_DIR, MODULES_DIR
    except ImportError:
        return dict.fromkeys(DJANGO_BUILTIN_COMMANDS, 'builtin')

    result = dict.fromkeys(DJANGO_BUILTIN_COMMANDS, 'builtin')
    for name in _get_app_commands_from_fs(Path(DJANGO_CORE_DIR), Path(MODULES_DIR)):
        if name not in result:
            result[name] = 'custom'
    return result


class CommandDiscovery:
    """
    Обнаружение Django команд без загрузки Django.

    Использует файловую систему и кэш discovered_apps.
    Django загружается только при run() команды.
    """

    def __init__(self):
        self._commands: Dict[str, Type[PoetryCommand]] = {}

    def _create_command_class(self, name: str, is_custom: bool = False) -> Type[PoetryCommand]:
        """Создаёт класс-обёртку для команды."""
        class_name = f"{name.title().replace('_', '')}Command"
        docstring = f"Команда для '{name}' ({'пользовательская' if is_custom else 'встроенная'})."

        return type(
            class_name,
            (PoetryCommand,),
            {
                '__doc__': docstring,
                'poetry_command_name': name,
                'django_command_name': name,
                '__init__': lambda self, n=name: PoetryCommand.__init__(self, n),
            }
        )

    def discover(self) -> Dict[str, Type[PoetryCommand]]:
        """Обнаружение команд без загрузки Django."""
        raw = _discover_commands_fast()
        self._commands = {}
        for name, cmd_type in raw.items():
            self._commands[name] = self._create_command_class(
                name, is_custom=(cmd_type == 'custom')
            )
        return self._commands

    def get_command(self, name: str) -> Optional[Type[PoetryCommand]]:
        """Возвращает класс команды по имени."""
        if not self._commands:
            self.discover()
        return self._commands.get(name)

    def get_all(self) -> Dict[str, Type[PoetryCommand]]:
        """Возвращает все обнаруженные команды."""
        if not self._commands:
            self.discover()
        return self._commands.copy()

    def rediscover(self) -> Dict[str, Type[PoetryCommand]]:
        """Сбрасывает кэш после смены ERGO_PROCESS_ROLE."""
        self._commands = {}
        return self.discover()


def _reset_process_app_caches() -> None:
    """Каталог модулей и discovered_apps читают роль процесса один раз."""
    try:
        from src.core.utils.module_registry import reset_cache

        reset_cache()
    except ImportError:
        pass
    try:
        from src.core.utils.auto_api.discovered_apps_cache import (
            clear_discovered_apps_memory_cache,
        )

        clear_discovered_apps_memory_cache()
    except ImportError:
        pass


def bind_process_to_command_owner(command_name: str) -> Optional[str]:
    """
    Если команда живёт в одном вынесенном модуле, ставит роль ``module:<name>``.

    Иначе Django на стороне ядра в microservice не видит management-команду модуля.
    """
    if (os.environ.get('ERGO_PROCESS_ROLE') or '').strip():
        return None

    _ensure_path()
    deployment = Path(__file__).resolve().parents[2] / 'deployment'
    if str(deployment) not in sys.path:
        sys.path.insert(0, str(deployment))
    try:
        from lifecycle.modules.cli_commands import find_modules_owning_management_command
        from src.config.settings.base import MODULES_DIR
        from src.core.utils.module_registry import (
            get_disabled_modules,
            is_module_disabled,
            is_module_loadable_in_process,
        )
    except ImportError:
        return None

    disabled = list(get_disabled_modules())

    owners = find_modules_owning_management_command(
        command_name,
        Path(MODULES_DIR),
        disabled=disabled,
    )
    if len(owners) != 1:
        return None

    owner = owners[0]
    if is_module_disabled(owner):
        return None
    if is_module_loadable_in_process(owner):
        return None

    os.environ['ERGO_PROCESS_ROLE'] = f'module:{owner}'
    _reset_process_app_caches()
    return owner


discovery = CommandDiscovery()
