"""Включён ли режим разработчика (только development + явный флаг)."""


def is_dev_tools_enabled() -> bool:
    from django.conf import settings

    return bool(getattr(settings, 'DEV_TOOLS_ENABLED', False))
