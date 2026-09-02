"""
Файл содержащий базовую конфигурацию для Django-приложения.
Он включает настройки базового каталога проекта.
"""

import os
from pathlib import Path

from src.config.ergo_runtime import media_access_mode
from src.config.nginx_runtime import (
    effective_media_public_host,
    effective_media_public_port,
    media_api_internal_base_url,
    media_api_public_base_url,
    nginx_use_https,
)
from src.config.paths import (
    CACHE_DIR,
    ENV_FILE_PATH,
    MODULES_DIR,
    SYSTEM_DIR,
    VIRTUAL_ENV_DIR,
)

"""
Определяет базовый каталог проекта.

BASE_DIR используется для построения путей к различным ресурсам проекта, таким как шаблоны, статические файлы и т.д.
"""
# Получаем путь к корневой директории проекта (ergo_ms/api/src)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Получаем путь к директории api (ergo_ms/api)
API_DIR = Path(__file__).resolve().parent.parent.parent.parent

# Django-пакет src.core (не путать с REPO_CORE_DIR / CORE_DIR из paths.py).
DJANGO_CORE_DIR = BASE_DIR / 'core'

# Корневая директория для ресурсов.
RESOURCES_DIR = VIRTUAL_ENV_DIR / 'resources'

# Корневая директория для обученных моделей (модули кладут артефакты сюда).
TRAINED_MODELS_PATH = VIRTUAL_ENV_DIR / 'trained_models'

# Корневая директория для сторонних программ.
PACKAGES_PATH = VIRTUAL_ENV_DIR / 'packages'

# URL для доступа к статическим файлам.
STATIC_URL = '/static/'

# Корневая директория для статических файлов.
STATIC_ROOT = VIRTUAL_ENV_DIR / 'static_api'

# URL для FileField Django (ORM). Публичная раздача файлов — только через media_api (/serve/),
# не через этот префикс в HTTP.
MEDIA_URL = '/media/'

# Корневая директория для медиа файлов.
MEDIA_ROOT = os.path.join(SYSTEM_DIR, 'media')

# Корневая директория для сгенерированных документов.
GENERATED_DOCUMENTS_DIR = Path(MEDIA_ROOT) / 'generated_docs'

# Корневая директория для логов (ERGO_LOGS_DIR в .env или <корень>/logs).
from src.config.log_paths import resolve_logs_root

LOGS_ROOT = str(resolve_logs_root(SYSTEM_DIR))

# Хранилище для статических файлов, использующее Whitenoise для сжатия и кэширования.
STORAGES = {
    'default': {
        'BACKEND': 'src.core.utils.media_storage.MediaApiStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

# Media API (CDN / file server)
MEDIA_API_PUBLIC_BASE_URL = media_api_public_base_url()
MEDIA_API_INTERNAL_URL = media_api_internal_base_url()
# Компоненты публичного URL (override; в .env можно не задавать при MEDIA_API_URL)
MEDIA_API_HOST = effective_media_public_host('localhost')
MEDIA_API_PORT = int(effective_media_public_port('8003'))
MEDIA_API_PROTOCOL = 'https' if nginx_use_https() else os.getenv('MEDIA_API_PROTOCOL', 'http')
MEDIA_URL_EXPIRATION = int(os.getenv('MEDIA_URL_EXPIRATION', '3600'))
MEDIA_UPLOAD_MAX_SIZE = int(os.getenv('MEDIA_UPLOAD_MAX_SIZE', '524288000'))
# Абсолютный потолок: модуль может запросить выше MEDIA_UPLOAD_MAX_SIZE, но не выше hard.
MEDIA_UPLOAD_HARD_MAX_SIZE = int(os.getenv('MEDIA_UPLOAD_HARD_MAX_SIZE', str(5 * 1024 * 1024 * 1024)))
if MEDIA_UPLOAD_HARD_MAX_SIZE < MEDIA_UPLOAD_MAX_SIZE:
    MEDIA_UPLOAD_HARD_MAX_SIZE = MEDIA_UPLOAD_MAX_SIZE
MEDIA_UPLOAD_TOKEN_EXPIRATION = int(os.getenv('MEDIA_UPLOAD_TOKEN_EXPIRATION', '300'))

# Режим доступа core/api к файлам: ERGO_MEDIA (или явный MEDIA_ACCESS_MODE)
MEDIA_ACCESS_MODE = media_access_mode()
MEDIA_API_INTERNAL_KEY = os.getenv('MEDIA_API_INTERNAL_KEY', '').strip()

# Compute-пайплайн (см. core/utils/media_client/pipeline.py, scratch.py)
# Scratch — эфемерные файлы обработки (никогда в БД и не в signed URL)
MEDIA_SCRATCH_ROOT = os.getenv('MEDIA_SCRATCH_ROOT', '').strip() or str(CACHE_DIR / 'scratch')
# Cache — локальные копии canonical-файлов при MEDIA_ACCESS_MODE=remote (localize)
MEDIA_CACHE_ROOT = os.getenv('MEDIA_CACHE_ROOT', '').strip() or str(CACHE_DIR / 'media')
