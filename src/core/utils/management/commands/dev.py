"""
Файл для определения команды Django для запуска development сервера.

Этот файл содержит класс Command, который наследуется от RunserverCommand и предоставляет 
функциональность для запуска сервера разработки с настройками хоста и порта из конфигурации.

Пример использования:
>>> ergoms api runserver [host:port]
"""

import logging
import os
import subprocess
import sys

try:
    from daphne.management.commands.runserver import Command as RunserverCommand
except ImportError:
    from django.core.management.commands.runserver import Command as RunserverCommand
from django.core.management.base import CommandParser

from django.conf import settings

from src.config.deploy import build_daphne_command, get_api_bind_host, get_api_bind_port, is_production
from src.config.env import env
from src.config.paths import API_DIR
from src.core.utils.startup_timing import (
    StreamReadyWrapper,
    install_listening_ready_handler,
    remove_listening_ready_handler,
)

logger = logging.getLogger('core.utils.commands')

SERVICE_NAME = 'API'


def _env_autoreload_enabled() -> bool:
    """API_AUTORELOAD: false — runserver без reloader (быстрее cold start, без hot-reload)."""
    return env.bool('API_AUTORELOAD', default=True)


class Command(RunserverCommand):
    """
    Команда Django для запуска development сервера.
    
    Расширяет стандартную команду runserver для использования
    настроек хоста и порта из конфигурации проекта.
    """
    help = 'Запускает development сервер с необходимыми сервисами'

    def log_action(self, protocol, action, details):
        """HTTP access — только AccessLogMiddleware (без дубля django.channels.server)."""
        return

    def add_arguments(self, parser: CommandParser) -> None:
        """
        Добавляет аргументы командной строки.

        Args:
            parser: Парсер аргументов командной строки
        """
        super().add_arguments(parser)

    def handle(self, *args: tuple, **options: dict) -> None:
        """
        Выполняет команду запуска сервера.

        Если адрес и порт не указаны явно, использует значения из настроек.

        Args:
            *args: Позиционные аргументы
            **options: Именованные аргументы
        """
        if is_production():
            host = get_api_bind_host()
            port = get_api_bind_port()
            msg = f'{SERVICE_NAME} (запуск как на сервере): daphne на {host}, порт {port} (без autoreload)'
            logger.info(msg)
            try:
                self.stdout.write(self.style.SUCCESS(msg))
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
            cmd = build_daphne_command(sys.executable)
            raise SystemExit(subprocess.call(cmd, cwd=str(API_DIR)))

        if not _env_autoreload_enabled():
            options['use_reloader'] = False
            msg = 'API_AUTORELOAD=false: runserver без autoreload'
            logger.info(msg)
            try:
                self.stdout.write(self.style.WARNING(msg))
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass

        use_reloader = bool(options.get('use_reloader', True))
        if use_reloader:
            from src.core.utils.dev_autoreload import install_dev_autoreload_filters

            install_dev_autoreload_filters()

        is_reloader_child = os.environ.get('RUN_MAIN') == 'true'
        if use_reloader:
            role = (
                'autoreload child (рабочий процесс)'
                if is_reloader_child
                else 'autoreload parent (launcher)'
            )
        else:
            role = 'без autoreload'
        logger.info('Запуск команды runserver (%s)', role)

        if not options['addrport']:
            server_host = getattr(settings, 'SERVER_HOST', None)
            server_port = getattr(settings, 'SERVER_PORT', None)

            if not all([server_host, server_port]):
                msg = 'SERVER_HOST или SERVER_PORT не настроены в конфигурации'
                logger.error(msg)
                raise ValueError(msg)

            addrport = f'{server_host}:{server_port}'
            logger.info(f'Используются настройки по умолчанию: {addrport}')
            options['addrport'] = addrport
        else:
            logger.info(f'Используются пользовательские настройки: {options["addrport"]}')

        listen_handler = None
        if is_reloader_child or not use_reloader:
            listen_handler = install_listening_ready_handler(
                SERVICE_NAME,
                stream=self.stdout,
            )

        try:
            orig_stdout = self.stdout
            self.stdout = StreamReadyWrapper(orig_stdout, SERVICE_NAME)
            # Stream обёртка — тот же поток, что у Listening handler.
            if listen_handler is not None:
                listen_handler._stream = self.stdout
            try:
                super().handle(*args, **options)
            finally:
                self.stdout = orig_stdout
        except Exception as e:
            msg = f'Ошибка при запуске сервера: {str(e)}'
            logger.error(msg)
            raise
        finally:
            remove_listening_ready_handler(listen_handler)
