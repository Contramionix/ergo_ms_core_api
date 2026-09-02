"""
Локализация Django (ru / en / fr).

DEFAULT_LANGUAGE в .env — язык API и новых профилей.
TIME_ZONE в .env — часовой пояс приложения и меток в логах (IANA).
Свой язык пользователь меняет только в настройках (UserProfile.language).
"""

from src.config.env import env
from src.config.settings.base import API_DIR

# Поддерживаемые языки UI и API-сообщений.
LANGUAGES = [
    ('ru', 'Русский'),
    ('en', 'English'),
    ('fr', 'Français'),
]

# Допустимые коды языка для UserProfile.language.
SUPPORTED_UI_LANGUAGES = frozenset(code for code, _name in LANGUAGES)


def _resolve_default_language() -> str:
    raw = env.str('DEFAULT_LANGUAGE', default='ru').strip().lower()
    code = raw.split('-', 1)[0] if raw else 'ru'
    if code in SUPPORTED_UI_LANGUAGES:
        return code
    return 'ru'


# Код языка по умолчанию (из .env DEFAULT_LANGUAGE).
LANGUAGE_CODE = _resolve_default_language()

# Каталоги gettext проекта (ядро API).
LOCALE_PATHS = [
    str(API_DIR / 'locale'),
]

USE_I18N = True


def _resolve_time_zone() -> str:
    raw = env.str('TIME_ZONE', default='UTC').strip()
    return raw or 'UTC'


# Часовой пояс приложения и меток времени в логах (из .env TIME_ZONE).
TIME_ZONE = _resolve_time_zone()

# Использование временных зон.
USE_TZ = True
