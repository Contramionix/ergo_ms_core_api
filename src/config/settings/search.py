"""Настройки централизованного поиска (Meilisearch)."""

from django.core.exceptions import ImproperlyConfigured

from src.config.deploy import is_production
from src.config.env import env

_TEMPLATE_MEILI_KEY = 'ergo_ms_dev_meili_key'

ERGO_SEARCH_ENABLED = env.bool('ERGO_SEARCH_ENABLED', default=True)
MEILI_HOST = (env('MEILI_HOST', default='http://127.0.0.1:8004') or '').strip().rstrip('/')
# Должен совпадать с ключом процесса Meilisearch (install_meilisearch / служба).
MEILI_MASTER_KEY = (env('MEILI_MASTER_KEY', default='') or '').strip()
MEILI_SEARCH_TIMEOUT_SEC = float(env('MEILI_SEARCH_TIMEOUT_SEC', default='5.0'))

if ERGO_SEARCH_ENABLED and is_production():
    key = MEILI_MASTER_KEY.lower()
    if not MEILI_MASTER_KEY or key == _TEMPLATE_MEILI_KEY:
        raise ImproperlyConfigured(
            'MEILI_MASTER_KEY пуст или совпадает с шаблоном. '
            'Задайте ключ через ergoms generate-secret или env/search.env.'
        )
