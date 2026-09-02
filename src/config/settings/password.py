"""
Настройки политики паролей из переменных окружения (.env).
Используются в AUTH_PASSWORD_VALIDATORS и ADP (регистрация, смена пароля).
"""

from src.config.env import env
from src.config.security_profile_runtime import password_min_length


def _env_bool(name: str, default: bool) -> bool:
    return env.bool(name, default=default)


# Unset → профиль ERGO_SECURITY (standard: 8, open: 6)
PASSWORD_MIN_LENGTH = password_min_length()
PASSWORD_MAX_LENGTH = env.int('API_PASSWORD_MAX_LENGTH', default=128)
PASSWORD_REQUIRE_LOWERCASE = _env_bool('API_PASSWORD_REQUIRE_LOWERCASE', True)
PASSWORD_REQUIRE_UPPERCASE = _env_bool('API_PASSWORD_REQUIRE_UPPERCASE', False)
PASSWORD_REQUIRE_DIGIT = _env_bool('API_PASSWORD_REQUIRE_DIGIT', True)
PASSWORD_REQUIRE_SPECIAL = _env_bool('API_PASSWORD_REQUIRE_SPECIAL', False)

PASSWORD_VALIDATE_USER_ATTRIBUTE_SIMILARITY = _env_bool(
    'API_PASSWORD_VALIDATE_USER_ATTRIBUTE_SIMILARITY', True,
)
PASSWORD_VALIDATE_COMMON_PASSWORD = _env_bool('API_PASSWORD_VALIDATE_COMMON_PASSWORD', True)
PASSWORD_VALIDATE_NUMERIC_ONLY = _env_bool('API_PASSWORD_VALIDATE_NUMERIC_ONLY', True)

PASSWORD_POLICY = {
    'min_length': PASSWORD_MIN_LENGTH,
    'max_length': PASSWORD_MAX_LENGTH,
    'require_lowercase': PASSWORD_REQUIRE_LOWERCASE,
    'require_uppercase': PASSWORD_REQUIRE_UPPERCASE,
    'require_digit': PASSWORD_REQUIRE_DIGIT,
    'require_special': PASSWORD_REQUIRE_SPECIAL,
}


def _build_auth_password_validators():
    validators = []

    if PASSWORD_VALIDATE_USER_ATTRIBUTE_SIMILARITY:
        validators.append({
            'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
        })

    validators.append({
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': PASSWORD_MIN_LENGTH},
    })

    if PASSWORD_MAX_LENGTH > 0:
        validators.append({
            'NAME': 'src.core.cms.adp.password_policy.MaxLengthValidator',
            'OPTIONS': {'max_length': PASSWORD_MAX_LENGTH},
        })

    if PASSWORD_VALIDATE_COMMON_PASSWORD:
        validators.append({
            'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
        })

    if PASSWORD_VALIDATE_NUMERIC_ONLY:
        validators.append({
            'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
        })

    validators.append({
        'NAME': 'src.core.cms.adp.password_policy.PasswordPolicyValidator',
    })

    return validators


AUTH_PASSWORD_VALIDATORS = _build_auth_password_validators()
