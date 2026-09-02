"""
Файл конфигурации Django приложения/модуля ADP.

Этот файл содержит класс конфигурации приложения, который определяет основные настройки,
такие как имя приложения и настройки базы данных по умолчанию.

Класс `AdpConfig`:
    Определяет конфигурацию приложения ADP, включая:
    - Тип поля первичного ключа по умолчанию
    - Имя приложения в системе
"""

from django.apps import AppConfig


def create_system_roles_signal_handler(sender, **kwargs):
    """Создает системные роли после применения миграций."""
    from django.db import OperationalError, ProgrammingError

    from src.core.cms.adp.services.permissions import PermissionService

    try:
        PermissionService.ensure_system_roles()
    except (OperationalError, ProgrammingError):
        pass
    except Exception:
        pass


class AdpConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'src.core.cms.adp'
    label = 'cms_adp'

    def ready(self):
        from django.db.models.signals import post_migrate

        from src.core.utils.django_cli import is_lean_schema_cli

        # post_migrate нужен и при lean migrate (системные роли после схемы)
        post_migrate.connect(
            create_system_roles_signal_handler,
            sender=self,
            dispatch_uid='cms_adp.create_system_roles',
        )

        if is_lean_schema_cli():
            return

        from src.core.cms.adp import signals  # noqa: F401
        from src.core.cms.adp import integrations as _adp_integrations  # noqa: F401

        signals.connect_user_signals()
