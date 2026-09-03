"""Platform-ops пакетов справки: публикация ядра и подпись чтения."""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError

from src.core.integrations import bridge
from src.core.integrations.module_contracts import (
    CORE_KNOWLEDGE_PACK,
    CORE_KNOWLEDGE_SIGN_READ,
    CORE_KNOWLEDGE_USER_CAPABILITIES,
)
from src.core.utils.knowledge_pack import (
    current_core_pack,
    restore_pack_descriptors_from_media,
    sign_knowledge_read,
)

logger = logging.getLogger('integrations.knowledge')


@bridge.provide_op(CORE_KNOWLEDGE_PACK)
def _core_knowledge_pack(**_):
    return current_core_pack()


@bridge.provide_op(CORE_KNOWLEDGE_SIGN_READ)
def _core_knowledge_sign_read(*, path: str = '', **_):
    try:
        return sign_knowledge_read(path, owner=None)
    except (ValidationError, ValueError) as exc:
        logger.info('Отказ подписи пакета справки: %s', exc)
        return None


@bridge.provide_op(CORE_KNOWLEDGE_USER_CAPABILITIES)
def _core_knowledge_user_capabilities(
    *,
    user_public_id=None,
    full: bool = False,
    session_claims=None,
    **_,
):
    from src.core.utils.knowledge_capabilities import user_capabilities_op

    try:
        return user_capabilities_op(
            user_public_id=user_public_id,
            full=full,
            session_claims=session_claims,
        )
    except Exception:
        logger.warning('Не удалось собрать меню и каталог модулей', exc_info=True)
        return None


def load_knowledge_providers() -> None:
    """Восстановить дескрипторы с диска и подпись чтения процесса модуля."""
    from src.core.utils.knowledge_pack import (
        _current_module_name,
        _is_core_process,
        register_module_knowledge_sign_read,
    )

    if not _is_core_process():
        # Иначе CLI на хосте модулей читает пустой current.json у себя и не идёт на ядро.
        bridge.unregister(CORE_KNOWLEDGE_PACK)
        bridge.unregister(CORE_KNOWLEDGE_SIGN_READ)
        bridge.unregister(CORE_KNOWLEDGE_USER_CAPABILITIES)
    module_name = _current_module_name()
    if module_name:
        register_module_knowledge_sign_read(module_name)
    try:
        restore_pack_descriptors_from_media()
    except Exception:
        logger.debug('Не удалось восстановить пакеты справки с media', exc_info=True)
