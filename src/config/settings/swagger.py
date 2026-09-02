"""
Настройки доступа к документации API (Swagger UI / ReDoc) из переменных окружения (.env).
"""

from src.config.env import env
from src.config.deploy import is_development

_default_swagger_enabled = is_development()

SWAGGER_ENABLED = env.bool('API_SWAGGER_ENABLED', default=_default_swagger_enabled)

SPECTACULAR_SETTINGS = {
    'TITLE': 'ERGO MS API',
    'DESCRIPTION': 'API эргономичной системы',
    'VERSION': 'v2.1',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
    'SCHEMA_PATH_PREFIX': '/api/',
}
