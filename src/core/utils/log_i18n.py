"""
Сообщения API-логов из core/deployment/locales/<lang>/cli_messages.yaml.

Язык: ERGO_CLI_LANGUAGE → системная локаль → ru (cli_locale).
"""

from __future__ import annotations

import sys
from pathlib import Path

# core/api/src/core/utils → core/deployment
_DEPLOYMENT_DIR = Path(__file__).resolve().parents[4] / 'deployment'
_PROJECT_ROOT = _DEPLOYMENT_DIR.parent.parent

_ready = False


def log_t(key: str, **params: object) -> str:
    """Шаблон из cli_messages; при первом вызове подтягивает .env для языка CLI."""
    global _ready
    if str(_DEPLOYMENT_DIR) not in sys.path:
        sys.path.insert(0, str(_DEPLOYMENT_DIR))
    if not _ready:
        from cli_locale import (
            clear_locale_caches,
            ensure_project_env_loaded,
            resolve_cli_language,
        )

        ensure_project_env_loaded(_PROJECT_ROOT)
        clear_locale_caches()
        resolve_cli_language(project_root=_PROJECT_ROOT)
        _ready = True
    from cli_locale import t

    return t(key, **params)
