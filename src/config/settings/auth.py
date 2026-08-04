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
from src.config.settings.drf import DRF_BROWSABLE_ENABLED

AUTH_USER_MODEL = 'cms_adp.ErgoUser'

# Настройка ограничения запросов для анонимных и аутентифицированных пользователей.
THROTTLE_RATES_ANON = env.str('API_THROTTLE_RATES_ANON', default='10/minute')
THROTTLE_RATES_USER = env.str('API_THROTTLE_RATES_USER', default='5000/hour')

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
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': THROTTLE_RATES_ANON,
        'user': THROTTLE_RATES_USER,
        'password_reset': '5/minute',
        'login': '5/minute',
    },
}

# Установка настроек REST_FRAMEWORK глобально
if not hasattr(settings, 'REST_FRAMEWORK'):
    setattr(settings, 'REST_FRAMEWORK', REST_FRAMEWORK)

# Настройка время жизни токенов доступа и обновления (стандартные).
ACCESS_TOKEN_LIFETIME = env.int('API_ACCESS_TOKEN_LIFETIME', default=30)
REFRESH_TOKEN_LIFETIME = env.int('API_REFRESH_TOKEN_LIFETIME', default=1440)

# Настройка времени жизни токенов для режима "Запомнить меня" (в минутах).
# По умолчанию: 3 дня для access, 7 дней для refresh
REMEMBER_ME_ACCESS_TOKEN_LIFETIME = env.int('API_REMEMBER_ME_ACCESS_TOKEN_LIFETIME', default=4320)
REMEMBER_ME_REFRESH_TOKEN_LIFETIME = env.int('API_REMEMBER_ME_REFRESH_TOKEN_LIFETIME', default=10080)

# Тип развертывания (используется в других частях API, не влияет на JWT)
from src.config.deploy import get_deploy_type, is_development

DEPLOY_TYPE = get_deploy_type()
IS_DEVELOPMENT = is_development()

# Ограничение срока жизни JWT (true/false, не зависит от API_DEPLOY_TYPE).
# true  — используются API_ACCESS_TOKEN_LIFETIME и API_REFRESH_TOKEN_LIFETIME
# false — срок жизни не ограничивается (значения lifetime игнорируются);
#         только для development / open-профиля — в production запрещено.
JWT_LIFETIME_ENABLED = env.bool('API_JWT_LIFETIME_ENABLED', default=True)

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