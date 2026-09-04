"""
Точка входа для выполнения Django команд через Poetry.
"""

import logging
import os
import sys
from difflib import get_close_matches

_BOOTSTRAP_COMMANDS = frozenset({
    'install',
    'update',
    'module-add',
    'module-remove',
    'module-list',
    'warmup_caches',
    'warmup_celery',
})

# Schema one-shot: без discovery всех команд и без тяжёлого logging bootstrap.
# Список синхронизирован с src.core.utils.django_cli.LEAN_SCHEMA_COMMANDS.
_LEAN_SCHEMA_COMMANDS = frozenset({
    'migrate',
    'makemigrations',
    'showmigrations',
    'sqlmigrate',
    'squashmigrations',
})


def _setup_test_env_early():
    """
    Устанавливает переменные окружения для тестов ДО загрузки настроек Django.
    Должна вызываться до любых импортов из src.config.
    """
    if len(sys.argv) >= 2 and sys.argv[1] == 'test':
        args = sys.argv[2:]

        if '--full' in args:
            os.environ['TEST_FULL_APPS'] = '1'
            return

        for arg in args:
            if arg.startswith('-'):
                continue
            if arg.startswith('modules.'):
                parts = arg.split('.')
                if len(parts) >= 2:
                    os.environ['TEST_TARGET_MODULE'] = parts[1]
                    os.environ['DJANGO_SETTINGS_MODULE'] = 'src.config.patterns.test'
                    return


_setup_test_env_early()


def _configure_stdio_utf8() -> None:
    """UTF-8 для stdio; errors=replace — Docker/Windows (CP1251) не роняет interactive-команды."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8', errors='replace')
            except (AttributeError, OSError, ValueError):
                pass
    _patch_getpass_unicode_errors()


def _patch_getpass_unicode_errors() -> None:
    """getpass читает /dev/tty отдельно от sys.stdin — ловим CP1251 с Windows-терминала."""
    import getpass as getpass_mod

    if getattr(getpass_mod, '_ergo_utf8_replace_patched', False):
        return

    original = getpass_mod.getpass

    def _getpass(prompt='Password: ', stream=None):
        try:
            return original(prompt, stream)
        except UnicodeDecodeError as exc:
            raise SystemExit(
                '[ERROR] Не удалось прочитать пароль (кодировка терминала Windows → Docker).\n'
                '[INFO] Задайте пароль латиницей или без интерактива:\n'
                '  export DJANGO_SUPERUSER_USERNAME=admin\n'
                '  export DJANGO_SUPERUSER_PASSWORD=\'...\'\n'
                '  export DJANGO_SUPERUSER_EMAIL=admin@example.com\n'
                '  ergoms api createsuperuser --noinput'
            ) from exc

    getpass_mod.getpass = _getpass
    getpass_mod._ergo_utf8_replace_patched = True


def _run_bootstrap_command() -> None:
    """Установка зависимостей и прогрев кэшей без Django/Celery — для свежего venv и setup-full."""
    from commands.install import InstallCommand
    from commands.module_add import ModuleAddCommand, ModuleListCommand, ModuleRemoveCommand
    from commands.update import UpdateCommand
    from commands.warmup import WarmupCachesCommand, WarmupCeleryCommand

    _configure_stdio_utf8()

    command_map = {
        'install': InstallCommand,
        'update': UpdateCommand,
        'module-add': ModuleAddCommand,
        'module-remove': ModuleRemoveCommand,
        'module-list': ModuleListCommand,
        'warmup_caches': WarmupCachesCommand,
        'warmup_celery': WarmupCeleryCommand,
    }

    if len(sys.argv) < 2:
        print(
            'Использование: ergoms api '
            '<install|update|module-add|module-remove|module-list|'
            'warmup_caches|warmup_celery> '
            '[аргументы...]'
        )
        sys.exit(1)

    command_name = sys.argv[1]
    command_class = command_map.get(command_name)
    if command_class is None:
        print(f'Неизвестная bootstrap-команда: {command_name}')
        sys.exit(1)

    try:
        exit_code = command_class().run(*sys.argv[2:])
        sys.exit(exit_code if exit_code is not None else 0)
    except Exception as exc:
        print(f'Ошибка при выполнении команды {command_name}: {exc}', file=sys.stderr)
        sys.exit(1)


def _run_lean_schema_command() -> None:
    """migrate/makemigrations и др. — без discovery и без импорта LOGGING/startup_timing."""
    from commands.base import PoetryCommand
    from src.core.utils.django_cli import mark_lean_schema_cli

    mark_lean_schema_cli()
    _configure_stdio_utf8()

    command_name = sys.argv[1]
    args = sys.argv[2:]
    try:
        exit_code = PoetryCommand.for_django(command_name).run(*args)
        sys.exit(exit_code if exit_code is not None else 0)
    except Exception as exc:
        print(f'Ошибка при выполнении команды {command_name}: {exc}', file=sys.stderr)
        sys.exit(1)


def _run_full_main() -> None:
    from typing import Dict, Type

    from commands.base import PoetryCommand
    from commands.discovery import discovery
    from commands.install import InstallCommand
    from commands.module_add import ModuleAddCommand, ModuleListCommand, ModuleRemoveCommand
    from commands.update import UpdateCommand
    from commands.warmup import WarmupCachesCommand, WarmupCeleryCommand
    from src.config.settings.logger import LOGGING
    from src.core.utils.startup_timing import mark_start

    mark_start()

    logger = logging.getLogger('commands')
    logger.propagate = False
    verbose = LOGGING['formatters']['verbose']
    formatter = logging.Formatter(
        fmt=verbose['format'],
        style=verbose['style'],
        datefmt=verbose.get('datefmt'),
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    if not logger.handlers:
        logger.addHandler(console_handler)
    logger.setLevel(logging.INFO)

    script_commands: list[Type[PoetryCommand]] = [
        InstallCommand,
        UpdateCommand,
        ModuleAddCommand,
        ModuleRemoveCommand,
        ModuleListCommand,
        WarmupCachesCommand,
        WarmupCeleryCommand,
    ]

    def get_commands() -> Dict[str, Type[PoetryCommand]]:
        try:
            commands = discovery.get_all()
        except Exception as exc:
            logger.warning('Ошибка при загрузке команд: %s', exc)
            commands = {}

        for cmd_class in script_commands:
            commands[cmd_class.poetry_command_name] = cmd_class

        return commands

    def suggest_commands(command_name: str, commands: dict) -> list[str]:
        names = sorted(commands.keys())
        suggestions = get_close_matches(command_name, names, n=5, cutoff=0.5)
        if suggestions:
            return suggestions

        normalized = command_name.replace('-', '_')
        if normalized != command_name:
            suggestions = get_close_matches(normalized, names, n=5, cutoff=0.5)
            if suggestions:
                return suggestions

        alt = command_name.replace('_', '-')
        if alt != command_name:
            return get_close_matches(alt, names, n=5, cutoff=0.5)

        return []

    def print_unknown_command_help(command_name: str, commands: dict) -> None:
        logger.error('Неизвестная команда Django: %s', command_name)
        suggestions = suggest_commands(command_name, commands)
        if suggestions:
            formatted = ', '.join(f'ergoms api {name}' for name in suggestions)
            logger.error('Возможно, вы имели в виду: %s', formatted)
        else:
            logger.error('Команд Django: %d', len(commands))
        logger.error('Справка ergoms (не Django): ergoms help')

    _configure_stdio_utf8()
    commands = get_commands()

    if len(sys.argv) < 2:
        logger.error('Использование: ergoms api <команда> [аргументы...]')
        logger.error(
            'Команд Django: %d. Список не выводится — при опечатке будут подсказки.',
            len(commands),
        )
        logger.error('Справка ergoms: ergoms help')
        sys.exit(1)

    command_name = sys.argv[1]
    args = sys.argv[2:]

    command_class = commands.get(command_name)
    if not command_class:
        from commands.discovery import bind_process_to_command_owner

        owner = bind_process_to_command_owner(command_name)
        if owner:
            logger.info(
                'Команда %s: процесс модуля %s (ERGO_PROCESS_ROLE=module:%s)',
                command_name,
                owner,
                owner,
            )
            discovery.rediscover()
            commands = get_commands()
            command_class = commands.get(command_name)
        if not command_class:
            print_unknown_command_help(command_name, commands)
            sys.exit(1)

    try:
        command_instance = command_class()
        exit_code = command_instance.run(*args)
        sys.exit(exit_code if exit_code is not None else 0)
    except Exception as exc:
        logger.error('Ошибка при выполнении команды %s: %s', command_name, exc)
        sys.exit(1)


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] in _BOOTSTRAP_COMMANDS:
        _run_bootstrap_command()
        return
    if len(sys.argv) >= 2 and sys.argv[1] in _LEAN_SCHEMA_COMMANDS:
        _run_lean_schema_command()
        return
    _run_full_main()


if __name__ == '__main__':
    main()
