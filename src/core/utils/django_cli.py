"""
Детекция one-shot schema-команд Django для облегчённого старта.

При ``migrate`` / ``makemigrations`` и родственных командах не нужен runtime-
bootstrap (bridge providers, realtime topics, isolation audit, URL модулей).
Модели и ``post_migrate`` остаются — схема БД должна применяться полностью.

Для приложений ``modules.*`` ядро центрально пропускает ``AppConfig.ready()``
(см. ``install_lean_module_ready_guard``) — в каждом модуле гейт писать не нужно.
Discovery URL модулей тоже пропускается (``ModuleDiscoverer._find_modules_urls``):
без ``ready()`` мост пуст, импорт urls потребителей был бы ошибкой.
В ядре ``src.core.*`` тяжёлый ready по-прежнему гейтится точечно через
``is_lean_schema_cli()`` (например ADP оставляет ``post_migrate``).
"""

from __future__ import annotations

import os
import sys
from typing import Optional, Sequence

LEAN_SCHEMA_COMMANDS = frozenset({
    'migrate',
    'makemigrations',
    'showmigrations',
    'sqlmigrate',
    'squashmigrations',
    'migrate_module',
    'move_module_schema',
    'move_core_schema',
})

ENV_FLAG = 'ERGO_LEAN_DJANGO_CLI'

_MODULE_APP_PREFIX = 'modules.'
_LEAN_MODULE_GUARD_INSTALLED = False


def mark_lean_schema_cli() -> None:
    """Помечает текущий процесс как lean schema CLI (до django.setup)."""
    os.environ[ENV_FLAG] = '1'


def _first_positional_command(argv: Sequence[str]) -> Optional[str]:
    for arg in argv[1:]:
        if not arg or arg.startswith('-'):
            continue
        return arg
    return None


def is_lean_schema_cli(argv: Optional[Sequence[str]] = None) -> bool:
    """True для migrate/makemigrations и др. schema one-shot команд."""
    if os.environ.get(ENV_FLAG) == '1':
        return True
    cmd = _first_positional_command(argv if argv is not None else sys.argv)
    return cmd in LEAN_SCHEMA_COMMANDS


def install_lean_module_ready_guard() -> None:
    """
    Центрально пропускает ``AppConfig.ready()`` у ``modules.*`` при lean CLI.

    Вызывать до ``django.setup()``. Модели модулей импортируются как обычно;
    не вызывается только runtime-регистрация в ``ready()``.
    """
    global _LEAN_MODULE_GUARD_INSTALLED

    if _LEAN_MODULE_GUARD_INSTALLED:
        return

    from django.apps.config import AppConfig

    original_create = AppConfig.create

    @classmethod
    def create(cls, entry):
        config = original_create.__func__(cls, entry)
        app_name = getattr(config, 'name', '') or ''
        if not app_name.startswith(_MODULE_APP_PREFIX):
            return config

        original_ready = config.ready

        def ready():
            if is_lean_schema_cli():
                return None
            return original_ready()

        config.ready = ready
        return config

    AppConfig.create = create
    _LEAN_MODULE_GUARD_INSTALLED = True


def prepare_lean_schema_django() -> None:
    """Если текущая команда — lean schema CLI, ставит флаг и guard модулей."""
    if not is_lean_schema_cli():
        return
    mark_lean_schema_cli()
    install_lean_module_ready_guard()


def prepare_celery_for_django() -> None:
    """
    Вызывать до ``django.setup()`` в CLI (``ergoms api`` / ``manage.py``).

    Иначе ``@shared_task`` при загрузке apps цепляется к анонимному
    ``Celery("default")`` с ``amqp://localhost``, а брокер из настроек Django
    не применяется. API делает то же явно в ``asgi.py`` / ``wsgi.py``.
    """
    from src.config.celery import celery_app as _celery_app  # noqa: F401
    from src.config.celery import ensure_django_celery_configured
    import django
    from django.conf import settings as django_settings

    original_setup = django.setup
    if not getattr(original_setup, '_ergo_celery_after_setup', False):
        def setup(*args, **kwargs):
            original_setup(*args, **kwargs)
            ensure_django_celery_configured()

        setup._ergo_celery_after_setup = True  # type: ignore[attr-defined]
        django.setup = setup

    if django_settings.configured:
        ensure_django_celery_configured()
