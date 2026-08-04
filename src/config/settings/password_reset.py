"""
Настройки восстановления пароля из переменных окружения (.env).

API_PASSWORD_RESET_ENABLED:
- true  — пользователи могут восстанавливать пароль через форму «Забыл пароль» (по умолчанию)
- false — самостоятельное восстановление пароля отключено

API_PASSWORD_RESET_CODE_TTL_MINUTES — срок жизни кода подтверждения (минуты).
API_PASSWORD_RESET_CODE_MAX_ATTEMPTS — максимум неверных попыток ввода кода.
"""

from src.config.env import env

PASSWORD_RESET_ENABLED = env.bool('API_PASSWORD_RESET_ENABLED', default=True)
PASSWORD_RESET_CODE_TTL_MINUTES = env.int('API_PASSWORD_RESET_CODE_TTL_MINUTES', default=15)
PASSWORD_RESET_CODE_MAX_ATTEMPTS = env.int('API_PASSWORD_RESET_CODE_MAX_ATTEMPTS', default=5)
