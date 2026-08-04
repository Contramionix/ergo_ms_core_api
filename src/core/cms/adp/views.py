from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

from src.core.utils.base.base_views import BaseAPIView
from src.core.cms.adp.auth_cookies import (
    clear_auth_cookies,
    get_refresh_token_from_request,
    set_prev_user_cookie,
)
from src.core.cms.adp.services.session_devices import revoke_logout_session
from src.core.audit.shortcuts import audit_log


def _user_id_from_payload(payload):
    if not payload:
        return None
    return payload.get(api_settings.USER_ID_CLAIM)


def _user_id_from_refresh_string(raw: str):
    """Достаёт USER_ID_CLAIM из refresh; InvalidToken не наследует TokenError."""
    if not raw:
        return None
    try:
        return _user_id_from_payload(RefreshToken(raw).payload)
    except (TokenError, InvalidToken):
        pass
    # Истёкший refresh: claims без проверки exp (токен всё равно сбрасываем).
    try:
        backend = RefreshToken.get_token_backend()
        return _user_id_from_payload(backend.decode(raw, verify=False))
    except Exception:
        return None


def _prev_user_id_from_logout_request(request):
    """
    user_id для cookie смены аккаунта.

    С Bearer (меню-logout) — request.user через JWTAuthentication.
    Иначе — refresh cookie (в т.ч. истёкший).
    """
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        return user.id

    return _user_id_from_refresh_string(get_refresh_token_from_request(request))


class LogoutView(BaseAPIView):
    """Отзыв refresh-сессии и очистка HttpOnly cookie (доступно без валидного access)."""

    # Идемпотентная очистка cookie: не режем throttle'ом — иначе шторм 401→logout
    # получает 429 и refresh-cookie так и не сбрасывается.
    throttle_classes = []
    # Plain JWT без привязки к устройству: logout должен очищать cookie и после
    # отзыва сессии устройства, когда device-bound access уже недействителен.
    authentication_classes = [JWTAuthentication]

    def post(self, request):
        response = Response(status=status.HTTP_204_NO_CONTENT)
        prev_user_id = _prev_user_id_from_logout_request(request)
        # Best-effort: blacklist / revoke устройства до очистки cookie.
        revoke_logout_session(request)
        if prev_user_id is not None:
            set_prev_user_cookie(response, prev_user_id)
        clear_auth_cookies(response)
        actor = request.user if getattr(request.user, 'is_authenticated', False) else None
        if actor is not None:
            audit_log('auth.logout', request=request, actor=actor)
        return response
