"""
Утилита для генерации подписанных URL и upload-токенов для медиа-сервиса.
HMAC-логика — в core.shared.media_hmac; media_api реимпортирует оттуда же.
"""

from django.conf import settings

from core.shared.media_hmac import create_upload_token, sign_url
from src.config.nginx_runtime import media_api_public_base_url, media_api_public_upload_url

from src.core.utils.media_upload_quota import cap_upload_rate, is_valid_quota_slug
from src.core.utils.media_upload_validation import (
    cap_max_size,
    filter_allowed_types,
    normalize_target_dir,
)


def _get_secret_key() -> str:
    return settings.SECRET_KEY


def _get_media_base_url() -> str:
    # Пустая строка за nginx — тот же origin, что SPA. Не подставлять
    # MEDIA_API_HOST / NGINX_PUBLIC_HOST: на хосте модулей это IP пира,
    # и браузер упирается в connect-src 'self'.
    base_url = getattr(settings, 'MEDIA_API_PUBLIC_BASE_URL', None)
    if base_url is not None:
        return str(base_url).rstrip('/')
    return media_api_public_base_url()


def get_signed_media_url(
    file_path: str,
    expires_in: int = None,
    *,
    as_attachment: bool = False,
) -> str:
    """
    Сгенерировать подписанный URL для доступа к медиафайлу.

    Args:
        file_path: относительный путь к файлу (например, 'avatars/123.jpg')
        expires_in: время жизни URL в секундах (по умолчанию из настроек)
        as_attachment: если True — media_api отдаёт Content-Disposition: attachment

    Returns:
        Подписанный URL для media_api (за nginx — относительный ``/serve/...``).
    """
    if expires_in is None:
        expires_in = getattr(settings, 'MEDIA_URL_EXPIRATION', 3600)

    signature, expires = sign_url(file_path, _get_secret_key(), expires_in)
    base_url = _get_media_base_url()
    path = f"/serve/{file_path}?signature={signature}&expires={expires}"
    url = f"{base_url}{path}" if base_url else path
    if as_attachment:
        url += '&download=1'
    return url


def get_signed_media_url_from_field(file_field, expires_in: int = None) -> str:
    """
    Сгенерировать подписанный URL из Django FileField/ImageField.

    Args:
        file_field: экземпляр FileField или ImageField
        expires_in: время жизни URL в секундах

    Returns:
        Подписанный URL или None если файл отсутствует.
    """
    if not file_field or not file_field.name:
        return None
    return get_signed_media_url(file_field.name, expires_in)


def generate_upload_token(
    user_id: int,
    target_dir: str = '',
    max_size: int = None,
    allowed_types: list = None,
    expires_in: int = None,
    *,
    quota: str = 'user',
    rate: str | None = None,
) -> str:
    """
    Сгенерировать upload-токен для загрузки файла в media_api.

    Параметры валидируются на сервере (нормализация пути, cap размера, whitelist типов).
    quota: user | admin | slug модуля. Для slug в токен кладётся rate (после cap).
    """
    target_dir = normalize_target_dir(target_dir)
    max_size = cap_max_size(max_size)
    allowed_types = filter_allowed_types(allowed_types)
    quota_norm = (quota or 'user').strip().lower()
    payload_rate = None
    if quota_norm in ('user', 'admin'):
        pass
    elif is_valid_quota_slug(quota_norm) and rate:
        payload_rate = cap_upload_rate(str(rate))
    else:
        quota_norm = 'user'

    if expires_in is None:
        expires_in = getattr(settings, 'MEDIA_UPLOAD_TOKEN_EXPIRATION', 300)

    payload = {
        'user_id': user_id,
        'target_dir': target_dir,
        'max_size': max_size,
        'quota': quota_norm,
    }
    if payload_rate:
        payload['rate'] = payload_rate
    if allowed_types:
        payload['allowed_types'] = allowed_types

    return create_upload_token(payload, _get_secret_key(), expires_in)


def get_upload_info(
    user_id: int,
    target_dir: str = '',
    max_size: int = None,
    allowed_types: list = None,
    *,
    quota: str = 'user',
    rate: str | None = None,
) -> dict:
    """
    Получить полную информацию для загрузки (URL + токен).

    Returns:
        Словарь с upload_url и token.
    """
    token = generate_upload_token(
        user_id,
        target_dir,
        max_size,
        allowed_types,
        quota=quota,
        rate=rate,
    )
    return {
        'upload_url': media_api_public_upload_url(target_dir),
        'token': token,
    }
