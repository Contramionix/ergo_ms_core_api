"""Сборка ответа session-bootstrap одним проходом."""

from django.conf import settings
from django.contrib.auth import get_user_model

User = get_user_model()

from src.core.cms.adp.menu.menu_cache import get_user_menu_payload
from src.core.cms.adp.models import UserProfile
from src.core.cms.adp.serializers import (
    CMSUserBasicSerializer,
    CMSUserMenuSerializer,
    CMSUserProfileSerializer,
)
from src.core.cms.adp.services.permissions import PermissionService
from src.core.cms.adp.services.permissions_snapshot_cache import get_user_permissions_payload
from src.core.settings.models import UserAvatar
from src.config.version import get_system_version


def _get_user_avatar_url(user) -> str | None:
    try:
        avatar = user.avatar
    except UserAvatar.DoesNotExist:
        avatar = None
    if avatar and avatar.image:
        return avatar.image.url
    return None


def build_realtime_config_payload() -> dict:
    """Тот же контракт, что GET /api/realtime/config/."""
    return {
        'transport': getattr(settings, 'REALTIME_TRANSPORT', 'websocket'),
        'capabilities': getattr(settings, 'REALTIME_CAPABILITIES', {}),
        'sse_keepalive_interval': getattr(settings, 'REALTIME_SSE_KEEPALIVE_INTERVAL', 25),
        'poll_intervals': {
            'presence': getattr(settings, 'REALTIME_POLL_PRESENCE_INTERVAL', 45),
            'notifications': getattr(settings, 'REALTIME_POLL_NOTIFICATIONS_INTERVAL', 15),
            'admin_presence': getattr(settings, 'REALTIME_POLL_ADMIN_PRESENCE_INTERVAL', 10),
            'messenger': getattr(settings, 'REALTIME_POLL_MESSENGER_INTERVAL', 5),
        },
    }


def _serialize_profile(user) -> dict:
    """Профиль без get_or_create — adp_profile может отсутствовать."""
    data = CMSUserBasicSerializer(user).data
    profile = UserProfile.objects.filter(user_id=user.pk).first()
    data['adp_profile'] = CMSUserProfileSerializer(profile).data if profile else None
    return data


def build_permissions_snapshot_payload(user, session_claims=None) -> dict:
    """Тот же контракт, что GET /api/cms/adp/my-permissions/."""
    return get_user_permissions_payload(user, session_claims=session_claims)


def build_session_bootstrap_payload(user, session_claims=None) -> dict:
    """Агрегированные данные для холодного старта клиента."""
    user = (
        User.objects
        .select_related('avatar')
        .filter(pk=user.pk)
        .first()
    )

    return {
        'user': CMSUserMenuSerializer(user).data,
        'menu': (
            get_user_menu_payload(user, session_claims=session_claims)
            if user is not None
            else {'menu_items': [], 'separators': []}
        ),
        'profile': _serialize_profile(user) if user is not None else {},
        'avatar_url': _get_user_avatar_url(user) if user is not None else None,
        'access_to_panel': PermissionService.can_access_admin_panel(user),
        'permissions': (
            build_permissions_snapshot_payload(user, session_claims=session_claims)
            if user is not None
            else None
        ),
        'realtime': build_realtime_config_payload(),
        'system_version': get_system_version(),
    }
