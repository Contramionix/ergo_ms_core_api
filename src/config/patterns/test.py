"""
Настройки для изолированного тестирования модулей.

Загружает только ядро + тестируемый модуль + его зависимости.
Целевой модуль определяется через переменную окружения TEST_TARGET_MODULE.

Если TEST_TARGET_MODULE не задан или TEST_FULL_APPS=1,
загружаются все модули (стандартное поведение).
"""

import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'src.config.patterns.test')

from src.config.env import env
from src.config.settings_loader import load_settings_modules

SECRET_KEY = env.str('API_SECRET_KEY')
DEBUG = True
ALLOWED_HOSTS = ['*']

load_settings_modules(
    globals(),
    deferred={'celery_beat', 'jupyter', 'apps'},
)

target_module = os.environ.get('TEST_TARGET_MODULE')
use_full_apps = os.environ.get('TEST_FULL_APPS', '').lower() in ('1', 'true', 'yes')

if target_module and not use_full_apps:
    from src.core.utils.test_isolation.module_deps import get_isolated_apps
    ALL_MODULES = get_isolated_apps(target_module)
    _isolation_mode = True
else:
    from src.core.utils.auto_api.discovered_apps_cache import get_discovered_apps
    ALL_MODULES = get_discovered_apps(use_cache=True)
    _isolation_mode = False

INSTALLED_APPS = ALL_MODULES + [
    'daphne',
    'channels',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'drf_spectacular',
    'django_celery_beat',
]

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer"
    }
}

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'src.core.utils.middleware.security_headers_middleware.SecurityHeadersMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'src.core.utils.middleware.profile_locale_middleware.ProfileLocaleMiddleware',
    'src.core.utils.middleware.session_context_middleware.SessionContextMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware'
]

# В тестах нарушения изоляции и контрактов моста — ошибка, не warn.
BRIDGE_CONTRACTS = 'raise'
BRIDGE_ISOLATION = 'raise'

if _isolation_mode:
    print(f"[TEST] Изолированный режим: модуль '{target_module}'")
    print(f"[TEST] Загружено приложений: {len(ALL_MODULES)}")
