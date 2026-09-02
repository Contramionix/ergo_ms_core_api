import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils.translation import gettext as _
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.throttling import SimpleRateThrottle
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from src.core.cms.adp.auth_cookies import (
    get_refresh_token_from_request,
    refresh_cookie_max_age,
    set_refresh_cookie,
)
from src.core.cms.adp.models import UserDevice
from src.core.cms.adp.services.jwt_platform_claims import (
    attach_platform_auth_claims,
    copy_platform_auth_claims,
)
from src.core.cms.adp.services.session_bootstrap import build_session_bootstrap_payload
from src.core.cms.adp.services.session_devices import (
    bind_device_to_refresh_token,
    is_device_session_active,
)
from src.config.settings.auth import REFRESH_TOKEN_LIFETIME

logger = logging.getLogger('core.cms.adp.token_refresh')


class DeviceBoundTokenRefreshSerializer(TokenRefreshSerializer):
    # refresh в теле не требуется — источник HttpOnly cookie
    refresh = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        refresh_value = get_refresh_token_from_request(self.context['request'])
        if not refresh_value:
            raise ValidationError(_('Refresh token отсутствует.'))
        attrs['refresh'] = refresh_value

        refresh = RefreshToken(refresh_value)
        device_id = refresh.payload.get('device_id')
        user_id = refresh.payload.get('user_id')
        user = None

        if device_id is None:
            raise ValidationError(_('Сессия завершена. Войдите снова.'))

        if user_id is not None:
            user = get_user_model().objects.filter(pk=user_id).first()
            if user is None:
                raise ValidationError(_('Сессия завершена. Войдите снова.'))
            if not is_device_session_active(user, device_id):
                raise ValidationError(_('Сессия завершена. Войдите снова.'))

        try:
            data = super().validate(attrs)
        except get_user_model().DoesNotExist:
            raise ValidationError(_('Сессия завершена. Войдите снова.')) from None

        # После ротации SimpleJWT кладёт новый refresh в data; jti устройства
        # нужно перепривязать, иначе revoke по UserDevice бьёт в старый токен.
        active_refresh = refresh
        rotated_value = data.get('refresh')
        if rotated_value:
            active_refresh = RefreshToken(rotated_value)
            try:
                device_pk = int(device_id)
            except (TypeError, ValueError):
                device_pk = None
            if device_pk is not None and user_id is not None:
                device = UserDevice.objects.filter(
                    pk=device_pk,
                    user_id=user_id,
                    is_active=True,
                ).first()
                if device is not None:
                    bind_device_to_refresh_token(device, active_refresh)

        access = active_refresh.access_token
        access['device_id'] = device_id
        if user is not None:
            attach_platform_auth_claims(active_refresh, user)
            attach_platform_auth_claims(access, user)
            if rotated_value:
                data['refresh'] = str(active_refresh)
        else:
            copy_platform_auth_claims(active_refresh.payload, access)
        data['access'] = str(access)
        return data


class TokenRefreshRateThrottle(SimpleRateThrottle):
    """Отдельный бакет для F5/restore; rate из settings или запасной 60/minute."""

    scope = 'token_refresh'

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        if ident is None:
            return None
        return self.cache_format % {'scope': self.scope, 'ident': ident}

    def get_rate(self):
        try:
            return super().get_rate()
        except ImproperlyConfigured:
            return '60/minute'


class DeviceBoundTokenRefreshView(TokenRefreshView):
    serializer_class = DeviceBoundTokenRefreshSerializer
    # Не AnonRateThrottle: F5 без Bearer иначе делит общий anon-бакет
    # с гостевыми запросами и выглядит как разлогин.
    throttle_classes = [TokenRefreshRateThrottle]

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200 and isinstance(response.data, dict):
            refresh_value = response.data.get('refresh') or get_refresh_token_from_request(request)
            if refresh_value:
                set_refresh_cookie(
                    response,
                    refresh_value,
                    refresh_cookie_max_age(timedelta(minutes=REFRESH_TOKEN_LIFETIME)),
                )
                # F5: один RTT — access + session-bootstrap (GET session-bootstrap остаётся для soft path)
                self._attach_session_bootstrap(response, refresh_value)
        return response

    @staticmethod
    def _attach_session_bootstrap(response, refresh_value: str) -> None:
        try:
            refresh = RefreshToken(refresh_value)
            user_id = refresh.payload.get('user_id')
            if user_id is None:
                return
            user = get_user_model().objects.filter(pk=user_id).first()
            if user is None:
                return
            organization_id = refresh.payload.get('organization_id')
            try:
                organization_id = int(organization_id) if organization_id is not None else None
            except (TypeError, ValueError):
                organization_id = None
            response.data['session_bootstrap'] = build_session_bootstrap_payload(
                user,
                organization_id=organization_id,
            )
        except Exception:
            logger.exception('Не удалось вложить session_bootstrap в token-refresh')
