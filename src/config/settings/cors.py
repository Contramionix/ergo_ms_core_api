"""
Конфигурация CORS (Cross-Origin Resource Sharing) для Django API.

CORS позволяет веб-приложениям делать запросы к ресурсам с другого домена.
При CORS_ALLOW_CREDENTIALS = True нельзя использовать CORS_ALLOW_ALL_ORIGINS = True:
браузер запрещает Access-Control-Allow-Origin: * с учётными данными, поэтому задаём явный список.

Dev-origins собираются из _CORS_DEV_HOSTS и _CORS_DEV_PORTS (localhost/127.0.0.1 и порты
5173, 8001, 3000, 8080, 8000, 8003) и подставляются только при ERGO_ENV=development,
если CORS_ALLOWED_ORIGINS и CORS_ALLOWED_ORIGIN_REGEXES не заданы.

В production (и любом не-development режиме) нужен явный список origins и/или regexes;
иначе запуск прерывается (ImproperlyConfigured).

При nginx перед Django в список дополнительно добавляется публичный origin прокси
(см. effective_cors_origins), без подмешивания localhost в production.

Для продакшена в .env задают:
  - CORS_ALLOWED_ORIGINS — полные origin'ы через запятую: https://app.example.com
  - или CORS_ALLOWED_ORIGIN_REGEXES — regex-шаблоны

При использовании прокси перед Django: запросы OPTIONS к /api/ должны проксироваться в Django;
ответ бэкенда не должен лишаться заголовков CORS.
"""

from django.core.exceptions import ImproperlyConfigured

from src.config.deploy import is_development
from src.config.env import env
from src.config.nginx_runtime import effective_cors_origins

CORS_ALLOW_ALL_ORIGINS = False

_CORS_DEV_PORTS = (5173, 8001, 3000, 8080, 8000, 8003)
_CORS_DEV_HOSTS = ('localhost', '127.0.0.1')
_default_origins = [
    f'http://{host}:{port}' for host in _CORS_DEV_HOSTS for port in _CORS_DEV_PORTS
]


def _parse_csv_list(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(',') if part.strip()]


_origins_raw = env.str('CORS_ALLOWED_ORIGINS', default='').strip()
_regexes_raw = env.str('CORS_ALLOWED_ORIGIN_REGEXES', default='').strip()

CORS_ALLOWED_ORIGIN_REGEXES = _parse_csv_list(_regexes_raw)

if _origins_raw:
    _resolved_origins = _parse_csv_list(_origins_raw)
elif CORS_ALLOWED_ORIGIN_REGEXES:
    _resolved_origins = []
elif is_development():
    _resolved_origins = list(_default_origins)
else:
    raise ImproperlyConfigured(
        'В production (и любом не-development режиме) задайте CORS_ALLOWED_ORIGINS '
        'или CORS_ALLOWED_ORIGIN_REGEXES. Список localhost подставляется только при '
        'ERGO_ENV=development.'
    )

CORS_ALLOWED_ORIGINS = effective_cors_origins(_resolved_origins)

CORS_ALLOW_CREDENTIALS = True
