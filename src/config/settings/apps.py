"""
Этот файл содержит настройки установленных приложений и middleware для Django-приложения.

Использует кэшированный discovery приложений (discovered_apps_cache) — результат
сохраняется в файл и пересчитывается только при изменении core/ или modules/.
"""

from src.config.deploy import is_development
from src.config.env import env
from src.core.utils.auto_api.discovered_apps_cache import get_discovered_apps
from src.core.utils.module_registry import is_slim_module_process

ALL_MODULES = get_discovered_apps(use_cache=True)
_SLIM_MODULE = is_slim_module_process()

INSTALLED_APPS = ALL_MODULES + [
    'daphne',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'rest_framework',
    'rest_framework_simplejwt.token_blacklist',
    
    'corsheaders',
    'drf_spectacular',
]

if not _SLIM_MODULE:
    INSTALLED_APPS.insert(1, 'channels')
    INSTALLED_APPS.append('django_celery_beat')

if is_development():
    INSTALLED_APPS.append('django_extensions')

# CHANNEL_LAYERS — в src.config.settings.channel_layers

# Определяем список middleware
MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'src.core.utils.middleware.request_id_middleware.RequestIdMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'src.core.utils.middleware.security_headers_middleware.SecurityHeadersMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'src.core.cms.adp.middleware.session_hint_cookie.SessionHintCookieMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'src.core.utils.middleware.profile_locale_middleware.ProfileLocaleMiddleware',
    'src.core.utils.middleware.session_context_middleware.SessionContextMiddleware',
    'src.core.cms.adp.middleware.permission_request_cache.PermissionRequestCacheMiddleware',
    'src.core.cms.adp.dev_tools.middleware.DevToolsPreviewMiddleware',
    'src.core.cms.adp.middleware.api_access_policy.ApiAccessPolicyMiddleware',
    'src.core.audit.context.AuditContextMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    # В конце цепочки — финальный status_code; одна строка INFO (и daphne, и runserver)
    'src.core.utils.middleware.access_log_middleware.AccessLogMiddleware',
]

_security_index = MIDDLEWARE.index('django.middleware.security.SecurityMiddleware')
MIDDLEWARE.insert(
    _security_index + 1,
    'src.core.utils.middleware.maintenance_middleware.MaintenanceMiddleware',
)

if env.bool('API_GZIP_ENABLED', default=True):
    MIDDLEWARE.insert(_security_index + 2, 'django.middleware.gzip.GZipMiddleware')