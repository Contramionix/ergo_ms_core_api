from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

from src.core.utils.base.base_views import BaseAPIViewPublicMixin
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
    """Достаёт USER_ID_CLAIM из проверенного refresh. Истёкший токен не читаем."""
    if not raw:
        return None
    try:
        return _user_id_from_payload(RefreshToken(raw).payload)
    except (TokenError, InvalidToken):
        return None


def _prev_user_id_from_logout_request(request):
    """
    user_id для cookie смены аккаунта.

    С Bearer (меню-logout) — request.user через JWTAuthentication.
    Иначе — действующий refresh cookie.
    """
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        return user.id

    return _user_id_from_refresh_string(get_refresh_token_from_request(request))


class LogoutView(BaseAPIViewPublicMixin):
    """Отзыв refresh-сессии и очистка HttpOnly cookie (доступно без валидного access)."""

    # Идемпотентная очистка cookie: DRF throttle не ставим — первый logout
    # должен сбросить cookie. Повторный шторм: nginx zone=ergo_logout
    # (избыток → 204 без прокси) + fast-path без cookie.
    throttle_classes = []
    # Plain JWT без привязки к устройству: logout должен очищать cookie и после
    # отзыва сессии устройства, когда device-bound access уже недействителен.
    authentication_classes = [JWTAuthentication]

    def post(self, request):
        response = Response(status=status.HTTP_204_NO_CONTENT)
        refresh_raw = get_refresh_token_from_request(request)
        actor = request.user if getattr(request.user, 'is_authenticated', False) else None

        # Зависшая вкладка после очистки сессии: без DB/revoke/audit.
        if not refresh_raw and actor is None:
            clear_auth_cookies(response)
            return response

        prev_user_id = _prev_user_id_from_logout_request(request)
        # Best-effort: blacklist / revoke устройства до очистки cookie.
        revoke_logout_session(request)
        if prev_user_id is not None:
            set_prev_user_cookie(response, prev_user_id)
        clear_auth_cookies(response)
        if actor is not None:
            audit_log('auth.logout', request=request, actor=actor)
        return response
