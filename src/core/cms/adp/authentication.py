"""JWT-principal без чтения auth_user (уровень 3)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from rest_framework.exceptions import APIException, AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from src.core.cms.adp.services.jwt_claims_cache import (
    drop_device_snapshot,
    get_device_snapshot,
    set_device_snapshot,
)
from src.core.cms.adp.services.session_devices import is_device_session_active
from src.core.integrations import bridge
from src.core.integrations.module_contracts import SESSION_DEVICE_ACTIVE


class SessionCheckUnavailable(APIException):
    """Мост ядра не ответил: это не отзыв сессии, клиент не должен выходить."""

    status_code = 503
    default_detail = 'Не удалось проверить сессию. Повторите запрос.'
    default_code = 'session_check_unavailable'


def _jwt_claims_mode() -> bool:
    from django.conf import settings

    mode = (getattr(settings, 'MODULE_AUTH_MODE', 'orm') or 'orm').strip().lower()
    return mode == 'jwt_claims'


def _as_uuid(value):
    if not value:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _device_active_snapshot(active):
    """False/None — отказ. True или dict со ``active`` — снимок (старый bool без public_id)."""
    if active is None or active is False:
        return None
    if isinstance(active, dict):
        if not active.get('active', True):
            return None
        return active
    if active:
        return {}
    return None


class JwtPrincipal(SimpleNamespace):
    """Лёгкий пользователь из JWT (без ORM)."""

    is_authenticated = True
    is_active = True
    is_anonymous = False
    is_staff = False
    is_superuser = False

    def __str__(self) -> str:
        return str(getattr(self, 'username', '') or getattr(self, 'public_id', '') or self.pk)


# Повторный разбор JWT в DRF после ApiAccessPolicyMiddleware.
REQUEST_JWT_AUTH_ATTR = '_ergo_jwt_auth'


class DeviceBoundJWTAuthentication(JWTAuthentication):
    """
    JWT с проверкой device_id.

    MODULE_AUTH_MODE=orm (по умолчанию) — пользователь из БД ядра.
    MODULE_AUTH_MODE=jwt_claims — principal из claims, устройство и активность
    пользователя через мост ``session.device_active``. Успешный снимок кэшируется,
    чтобы не ходить на ядро на каждый запрос. Обрыв моста (429/сеть) — 503,
    не «сессия завершена». False от ядра — отзыв, без запасного ORM.
    """

    def authenticate(self, request):
        cached = getattr(request, REQUEST_JWT_AUTH_ATTR, None)
        if cached is not None:
            return cached
        result = super().authenticate(request)
        if result is not None:
            setattr(request, REQUEST_JWT_AUTH_ATTR, result)
        return result

    def get_user(self, validated_token):
        device_id = validated_token.get('device_id')
        if device_id is None:
            raise AuthenticationFailed('Сессия завершена. Войдите снова.')

        if _jwt_claims_mode():
            return self._principal_from_claims(validated_token, device_id)

        user = super().get_user(validated_token)
        if not is_device_session_active(user, device_id):
            raise AuthenticationFailed('Сессия завершена. Войдите снова.')
        return user

    def _principal_from_claims(self, validated_token, device_id):
        user_id = validated_token.get('user_id')
        public_id = validated_token.get('user_public_id')
        if user_id is None and not public_id:
            raise AuthenticationFailed('Сессия завершена. Войдите снова.')

        public_id_str = str(public_id) if public_id else ''
        cached = get_device_snapshot(user_id, device_id, public_id_str)
        if isinstance(cached, dict):
            return self._principal_from_snapshot(validated_token, user_id, public_id, cached)

        raw = bridge.call(
            SESSION_DEVICE_ACTIVE,
            user_id=user_id,
            device_id=device_id,
            user_public_id=public_id_str,
            default=None,
        )
        snapshot = _device_active_snapshot(raw) if raw is not None else None
        if snapshot is not None:
            set_device_snapshot(user_id, device_id, public_id_str, snapshot)
            return self._principal_from_snapshot(validated_token, user_id, public_id, snapshot)

        if raw is False or (isinstance(raw, dict) and not raw.get('active', True)):
            drop_device_snapshot(user_id, device_id, public_id_str)
            raise AuthenticationFailed('Сессия завершена. Войдите снова.')

        stale = get_device_snapshot(user_id, device_id, public_id_str)
        if isinstance(stale, dict):
            return self._principal_from_snapshot(validated_token, user_id, public_id, stale)
        raise SessionCheckUnavailable()

    def _principal_from_snapshot(self, validated_token, user_id, public_id, snapshot):
        pk = int(user_id) if user_id is not None else 0
        pid = _as_uuid(public_id) or _as_uuid(snapshot.get('user_public_id'))
        username = str(validated_token.get('username') or snapshot.get('username') or '')
        raw_super = validated_token.get('is_superuser')
        raw_staff = validated_token.get('is_staff')
        raw_admin = validated_token.get('is_admin')
        is_superuser = bool(raw_super) if raw_super is not None else bool(snapshot.get('is_superuser'))
        is_staff = bool(raw_staff) if raw_staff is not None else bool(snapshot.get('is_staff'))
        # JWT важнее снимка: пустой кэш {} от старого bool True не должен затирать is_admin.
        if raw_admin is not None:
            is_admin = bool(raw_admin)
        elif 'is_admin' in snapshot:
            is_admin = bool(snapshot.get('is_admin'))
        else:
            is_admin = is_superuser
        return JwtPrincipal(
            id=pk,
            pk=pk,
            public_id=pid,
            username=username,
            is_superuser=is_superuser,
            is_staff=is_staff,
            is_admin=is_admin,
        )
