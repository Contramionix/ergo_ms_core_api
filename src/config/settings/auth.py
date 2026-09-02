"""
Файл содержащий конфигурацию аутентификации и авторизации для Django-приложения.
Он включает настройки для валидации паролей, ограничения запросов, JWT-аутентификации
и документации Swagger.

Конфигурация включает:
- Валидаторы паролей
- Ограничения запросов для анонимных и аутентифицированных пользователей
- Настройки JWT-аутентификации
- Настройки для режима "Запомнить меня"
- Настройки Swagger для документации API
"""

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from src.config.env import env
from src.config.security_profile_runtime import (
    auth_lockout_max_attempts,
    jwt_lifetime_enabled,
    login_throttle_rate,
    remember_me_refresh_token_lifetime,
)
from src.config.settings.drf import DRF_BROWSABLE_ENABLED

AUTH_USER_MODEL = 'cms_adp.ErgoUser'

AUTHENTICATION_BACKENDS = [
    'src.core.cms.adp.backends.EmailOrUsernameModelBackend',
]

# Настройка ограничения запросов для анонимных и аутентифицированных пользователей.
# Anon должен выдерживать F5/boot публичных эндпоинтов; login — отдельный scope 5/min.
THROTTLE_RATES_ANON = env.str('API_THROTTLE_RATES_ANON', default='60/minute')
THROTTLE_RATES_USER = env.str('API_THROTTLE_RATES_USER', default='5000/hour')
# Login throttle: unset → профиль ERGO_SECURITY (standard: 5/minute)
THROTTLE_RATES_LOGIN = login_throttle_rate()
THROTTLE_RATES_PASSWORD_RESET = env.str(
    'API_THROTTLE_RATES_PASSWORD_RESET',
    default='5/minute',
)
THROTTLE_RATES_REGISTRATION = env.str(
    'API_THROTTLE_RATES_REGISTRATION',
    default='5/minute',
)
# Восстановление сессии по cookie (F5): отдельный бакет, не общий anon.
THROTTLE_RATES_TOKEN_REFRESH = env.str(
    'API_THROTTLE_RATES_TOKEN_REFRESH',
    default='60/minute',
)

# Блокировка после N неудачных логинов (0 = выкл). Unset → профиль (hardened 10 / maximum 5).
API_AUTH_LOCKOUT_MAX_ATTEMPTS = auth_lockout_max_attempts()
API_AUTH_LOCKOUT_WINDOW_SECONDS = env.int('API_AUTH_LOCKOUT_WINDOW_SECONDS', default=900)
API_AUTH_LOCKOUT_DURATION_SECONDS = env.int('API_AUTH_LOCKOUT_DURATION_SECONDS', default=900)

DEFAULT_RENDERER_CLASSES = [
    'rest_framework.renderers.JSONRenderer',
]
if DRF_BROWSABLE_ENABLED:
    DEFAULT_RENDERER_CLASSES.append('rest_framework.renderers.BrowsableAPIRenderer')

# Конфигурация Django REST Framework.
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'src.core.cms.adp.authentication.DeviceBoundJWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_RENDERER_CLASSES': DEFAULT_RENDERER_CLASSES,
    'EXCEPTION_HANDLER': 'src.core.utils.exception_handler.api_exception_handler',
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': THROTTLE_RATES_ANON,
        'user': THROTTLE_RATES_USER,
        'password_reset': THROTTLE_RATES_PASSWORD_RESET,
        'registration': THROTTLE_RATES_REGISTRATION,
        'login': THROTTLE_RATES_LOGIN,
        'token_refresh': THROTTLE_RATES_TOKEN_REFRESH,
    },
    'DEFAULT_SCHEMA_CLASS': 'src.core.utils.swagger.inspectors.UniqueRefNameAutoSchema',
}

# Всегда синхронизируем: иначе после reload view видит новый scope,
# а django.conf.settings держит старый DEFAULT_THROTTLE_RATES → 500.
setattr(settings, 'REST_FRAMEWORK', REST_FRAMEWORK)
try:
    from rest_framework.settings import api_settings as _drf_api_settings

    _drf_api_settings.reload()
except Exception:
    pass

# Настройка время жизни токенов доступа и обновления (стандартные).
ACCESS_TOKEN_LIFETIME = env.int('API_ACCESS_TOKEN_LIFETIME', default=30)
REFRESH_TOKEN_LIFETIME = env.int('API_REFRESH_TOKEN_LIFETIME', default=1440)

# Настройка времени жизни токенов для режима "Запомнить меня" (в минутах).
# По умолчанию: 3 дня для access, 7 дней для refresh
REMEMBER_ME_ACCESS_TOKEN_LIFETIME = env.int('API_REMEMBER_ME_ACCESS_TOKEN_LIFETIME', default=4320)
REMEMBER_ME_REFRESH_TOKEN_LIFETIME = remember_me_refresh_token_lifetime()

# Тип развертывания (используется в других частях API, не влияет на JWT)
from src.config.deploy import get_deploy_type, is_development

DEPLOY_TYPE = get_deploy_type()
IS_DEVELOPMENT = is_development()

# Overlay прав глобального админа (глазик). В production всегда выключен.
DEV_TOOLS_ENABLED = bool(env.bool('ERGO_DEV_TOOLS', default=False) and IS_DEVELOPMENT)

# Ограничение срока жизни JWT (true/false, не зависит от API_DEPLOY_TYPE).
# true  — используются API_ACCESS_TOKEN_LIFETIME и API_REFRESH_TOKEN_LIFETIME
# false — срок жизни не ограничивается (значения lifetime игнорируются);
#         только для development / open-профиля — в production запрещено.
# Unset: standard+ → true из профиля; open — не форсится (дефолт кода True).
JWT_LIFETIME_ENABLED = jwt_lifetime_enabled()

if not JWT_LIFETIME_ENABLED and not IS_DEVELOPMENT:
    raise ImproperlyConfigured(
        'API_JWT_LIFETIME_ENABLED=false запрещён при ERGO_ENV=production '
        '(и при любом не-development режиме). Включите ограничение срока жизни JWT '
        'или переключите окружение на development.'
    )

# Внутреннее значение при JWT_LIFETIME_ENABLED=false (JWT требует claim exp)
JWT_NO_EXPIRY_LIFETIME_MINUTES = 5256000

# Конфигурация JWT-аутентификации.
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(
        minutes=ACCESS_TOKEN_LIFETIME if JWT_LIFETIME_ENABLED else JWT_NO_EXPIRY_LIFETIME_MINUTES,
    ),
    'REFRESH_TOKEN_LIFETIME': timedelta(
        minutes=REFRESH_TOKEN_LIFETIME if JWT_LIFETIME_ENABLED else JWT_NO_EXPIRY_LIFETIME_MINUTES,
    ),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': env.str('API_JWT_SIGNING_KEY', default='') or env.str('API_SECRET_KEY'),
    'VERIFYING_KEY': None,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
    'AUTH_TOKEN_CLASSES': ('rest_framework_simplejwt.tokens.AccessToken',),
    'TOKEN_TYPE_CLAIM': 'token_type',
}


def get_token_lifetime(remember_me: bool = False) -> tuple:
    """
    Возвращает время жизни access и refresh токенов.

    При API_JWT_LIFETIME_ENABLED=false (только development) срок не ограничивается.
    При true — стандартные значения или remember_me.

    Returns:
        tuple: (access_lifetime, refresh_lifetime) в виде timedelta
    """
    if not JWT_LIFETIME_ENABLED:
        no_expiry = timedelta(minutes=JWT_NO_EXPIRY_LIFETIME_MINUTES)
        return no_expiry, no_expiry

    if remember_me:
        return (
            timedelta(minutes=REMEMBER_ME_ACCESS_TOKEN_LIFETIME),
            timedelta(minutes=REMEMBER_ME_REFRESH_TOKEN_LIFETIME),
        )

    return (
        timedelta(minutes=ACCESS_TOKEN_LIFETIME),
        timedelta(minutes=REFRESH_TOKEN_LIFETIME),
    )