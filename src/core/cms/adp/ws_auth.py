import logging

from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model

logger = logging.getLogger('core.cms.adp')

User = get_user_model()


@database_sync_to_async
def user_from_jwt_token(token: str):
    """Разбор access-токена для WebSocket (как DeviceBoundJWTAuthentication в REST)."""
    try:
        from rest_framework_simplejwt.tokens import AccessToken
        from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
    except Exception:
        logger.exception('rest_framework_simplejwt не установлен')
        return None

    try:
        validated = AccessToken(token)
    except (InvalidToken, TokenError):
        return None
    except Exception:
        logger.exception('Не удалось разобрать JWT для WebSocket')
        return None

    user_id = validated.get('user_id')
    if not user_id:
        return None

    device_id = validated.get('device_id')
    if device_id is None:
        return None

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return None

    from src.core.cms.adp.services.session_devices import is_device_session_active

    if not is_device_session_active(user, device_id):
        return None

    return user
