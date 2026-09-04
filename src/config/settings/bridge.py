"""
Настройки ModuleBridge — единого механизма межмодульного взаимодействия.

В монолитном режиме используются in-process реализации
``LocalTransport`` / ``LocalEventBus`` — никаких внешних зависимостей.

``MODULE_RUNTIME`` (корневой ``.env``):

    monolith      — все модули в одном API-процессе
    microservice  — модули из MICROSERVICE_MODULES — отдельные процессы

Детали microservice и bridge — ``env/modules.env`` (см. ``env/modules.env.example``).

``BRIDGE_TRANSPORT``:

    local  — in-process (по умолчанию)
    http   — hybrid: local provide + HTTP RPC на владельца op

``BRIDGE_COLOCATE``:

    auto — при http соседи на этой машине (loopback / тот же хост) грузятся
           в процесс и вызываются без HTTP; другой сервер остаётся HTTP
    on   — то же принудительно
    off  — каждый вынесенный модуль только через HTTP, даже на 127.0.0.1

``BRIDGE_EVENT_BUS``:

    local  — in-process (по умолчанию)
    redis  — локальные handlers + Redis pub/sub для других процессов

``BRIDGE_ISOLATION`` / ``BRIDGE_CONTRACTS`` — см. isolation.py / contract_validation.py.
"""

from __future__ import annotations

import warnings

from src.config.env import env

BRIDGE_TRANSPORT = env.str('BRIDGE_TRANSPORT', default='local').strip().lower()
BRIDGE_EVENT_BUS = env.str('BRIDGE_EVENT_BUS', default='local').strip().lower()

BRIDGE_ISOLATION = env.str('BRIDGE_ISOLATION', default='warn').strip().lower()
BRIDGE_CONTRACTS = env.str('BRIDGE_CONTRACTS', default='warn').strip().lower()

BRIDGE_INTERNAL_TOKEN = env.str('BRIDGE_INTERNAL_TOKEN', default='').strip()
BRIDGE_SERVICE_URLS = env.str('BRIDGE_SERVICE_URLS', default='').strip()
BRIDGE_CORE_URL = env.str('BRIDGE_CORE_URL', default='').strip()
BRIDGE_COLOCATE = env.str('BRIDGE_COLOCATE', default='auto').strip().lower()
BRIDGE_HTTP_TIMEOUT = env.float('BRIDGE_HTTP_TIMEOUT', default=10.0)
BRIDGE_HTTP_RETRIES = env.int('BRIDGE_HTTP_RETRIES', default=2)
# false — внутренний HTTP (мост, пакеты справки) без HTTP_PROXY; true — HTTP_PROXY + NO_PROXY
ERGO_HTTP_TRUST_ENV = env.bool('ERGO_HTTP_TRUST_ENV', default=False)
BRIDGE_REDIS_DB = env.int('BRIDGE_REDIS_DB', default=4)
# jwt_claims: несколько процессов модуля с одного IP бьют session.device_active
# на каждый запрос. 60/minute на ядре даёт 429 и ложный «сессия завершена».
BRIDGE_INTERNAL_RATE = env.str('BRIDGE_INTERNAL_RATE', default='600/minute')

# orm — пользователь из БД ядра; jwt_claims — principal из JWT (уровень 3)
MODULE_AUTH_MODE = env.str('MODULE_AUTH_MODE', default='orm').strip().lower()

_raw_runtime = env.str('MODULE_RUNTIME', default='monolith').strip().lower()
if _raw_runtime == 'split':
    warnings.warn(
        'MODULE_RUNTIME=split устарел; используйте microservice',
        DeprecationWarning,
        stacklevel=1,
    )
    MODULE_RUNTIME = 'microservice'
elif _raw_runtime == 'microservice':
    MODULE_RUNTIME = 'microservice'
else:
    MODULE_RUNTIME = 'monolith'

MICROSERVICE_MODULES = env.str('MICROSERVICE_MODULES', default='').strip()

ERGO_PROCESS_ROLE = env.str('ERGO_PROCESS_ROLE', default='').strip()
PROCESS_MODULES = env.str('PROCESS_MODULES', default='').strip()
