"""Общие хелперы и mixin для админ-управления пользователями."""
from django.contrib.auth import get_user_model
from django.db.models import Prefetch, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from rest_framework import status
from rest_framework.response import Response

from src.config.settings.auth import IS_DEVELOPMENT
from src.core.cms.adp.models import UserRole
from src.core.cms.adp.serializers import (
    CMSUserSerializer,
    RoleGroupSerializer,
    RoleSerializer,
)
from src.core.cms.adp.services import presence as presence_service
from src.core.cms.adp.services.permissions import PermissionService
from src.core.cms.adp.services.user_deletion import (
    UserDeletionBlockedError,
    delete_admin_user,
    revoke_user_auth,
)
from src.core.search.mixins import parse_search_pagination
from src.core.settings.models import UserAvatar
from src.core.utils.methods import generate_secure_random_password

User = get_user_model()


class _AdminUserTargetMixin:
    """Resolve admin target user by public_id."""

    def _resolve_target_user(self, request, *, ref=None, select_related=True):
        if ref is None:
            return None

        qs = User.objects.filter(public_id=ref)
        if select_related:
            qs = qs.select_related('adp_profile')
        return qs.first()


def _get_user_avatar_url(user):
    try:
        avatar = user.avatar
    except UserAvatar.DoesNotExist:
        avatar = None
    if avatar and avatar.image:
        return avatar.image.url
    return None


def _validate_admin_user_deletion(request, target_user):
    if request.user.id == target_user.id:
        return Response(
            {'error': _('Нельзя удалить собственную учётную запись.')},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if PermissionService.can_manage_users_as_global_admin(target_user):
        if not PermissionService.can_manage_users_as_global_admin(request.user):
            return Response(
                {'error': _('Нельзя удалить глобального администратора.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

    return None


def _validate_admin_user_suspend(request, target_user):
    if request.user.id == target_user.id:
        return Response(
            {'error': _('Нельзя приостановить собственную учётную запись.')},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if PermissionService.is_admin(target_user) and PermissionService.count_global_admins() <= 1:
        return Response(
            {'error': _('Нельзя приостановить последнего администратора системы.')},
            status=status.HTTP_400_BAD_REQUEST,
        )

    return None


def _set_admin_user_active(user, *, is_active: bool) -> None:
    if user.is_active == is_active:
        return
    user.is_active = is_active
    user.save(update_fields=['is_active'])
    if not is_active:
        revoke_user_auth(user)


def _perform_admin_user_deletion(user):
    try:
        delete_admin_user(user)
    except UserDeletionBlockedError as exc:
        payload = {
            'error': _(
                'Невозможно удалить пользователя: '
                'есть связанные данные, блокирующие удаление.'
            ),
        }
        if exc.detail:
            payload['details'] = exc.detail
        return Response(payload, status=status.HTTP_400_BAD_REQUEST)

    return None


def _apply_system_password_reset(user):
    generated_password = generate_secure_random_password()
    try:
        user.set_password(generated_password)
        user.save(update_fields=['password'])
    finally:
        del generated_password


def _is_manual_password_reset_request(request):
    if not IS_DEVELOPMENT:
        return False
    new_password = (request.data.get('new_password') or '').strip()
    confirm_password = (request.data.get('confirm_password') or '').strip()
    return bool(new_password and confirm_password)


def _build_admin_user_full_name(user):
    if hasattr(user, 'get_full_name'):
        return user.get_full_name() or user.username
    name_parts = []
    if user.last_name:
        name_parts.append(user.last_name)
    if user.first_name:
        name_parts.append(user.first_name)
    middle_name = getattr(user, 'middle_name', None)
    if middle_name:
        name_parts.append(middle_name)
    return " ".join(part for part in name_parts if part and str(part).strip()) or user.username


def _get_active_user_role_from_prefetch(user):
    active_roles = getattr(user, '_active_roles', None)
    return active_roles[0] if active_roles else None


def _get_admin_user_role_for_display(user):
    user_role = (
        UserRole.objects
        .filter(user=user, is_active=True)
        .select_related('role')
        .prefetch_related('role_groups')
        .first()
    )
    if user_role or PermissionService.is_admin(user):
        return user_role

    return PermissionService.get_user_role(user)


def _build_admin_user_list_item(user, user_role=None, admin_role=None, presence_entry=None):
    if user_role is None:
        user_role = _get_active_user_role_from_prefetch(user)

    role = PermissionService.resolve_display_role(
        user,
        user_role,
        admin_role=admin_role,
    )
    role_groups = list(user_role.role_groups.all()) if user_role else []

    if presence_entry is None:
        presence_entry = presence_service.PresenceEntry(is_online=False, last_seen=None)

    return {
        'user_id': user.id,
        'public_id': str(user.public_id) if getattr(user, 'public_id', None) else None,
        'username': user.username,
        'email': user.email or '',
        'full_name': _build_admin_user_full_name(user),
        'first_name': user.first_name or '',
        'last_name': user.last_name or '',
        'date_joined': user.date_joined,
        'last_login': user.last_login,
        'is_active': bool(user.is_active),
        'is_online': presence_entry.is_online,
        'last_seen': presence_entry.last_seen,
        'role': role,
        'role_groups': role_groups,
        'avatar_url': _get_user_avatar_url(user),
    }


def _parse_admin_users_pagination(request):
    return parse_search_pagination(request, default_page_size=12, max_page_size=100)


def _parse_online_only_param(request) -> bool:
    raw = (request.query_params.get('online_only') or '').strip().lower()
    return raw in ('true', '1', 'yes')


def _parse_presence_param(request) -> str | None:
    raw = (request.query_params.get('presence') or '').strip().lower()
    if raw in ('online', 'offline'):
        return raw
    if _parse_online_only_param(request):
        return 'online'
    return None


def _parse_role_id_param(request) -> int | None:
    raw = (request.query_params.get('role') or '').strip()
    if not raw:
        return None
    try:
        role_id = int(raw)
    except (TypeError, ValueError):
        return None
    if role_id < 1:
        return None
    return role_id


def _parse_day_bound_param(request, key: str, *, end_of_day: bool):
    raw = (request.query_params.get(key) or '').strip()
    if not raw:
        return None
    suffix = 'T23:59:59' if end_of_day else 'T00:00:00'
    parsed = parse_datetime(raw) or parse_datetime(f'{raw}{suffix}')
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _parse_admin_users_list_filters(request):
    return {
        'presence': _parse_presence_param(request),
        'role_id': _parse_role_id_param(request),
        'joined_from': _parse_day_bound_param(request, 'joined_from', end_of_day=False),
        'joined_to': _parse_day_bound_param(request, 'joined_to', end_of_day=True),
        'last_seen_from': _parse_day_bound_param(request, 'last_seen_from', end_of_day=False),
        'last_seen_to': _parse_day_bound_param(request, 'last_seen_to', end_of_day=True),
        'letter': _parse_last_name_letter_param(request),
    }


def _online_presence_q():
    cutoff = presence_service.get_presence_stale_cutoff()
    return Q(
        presence__connection_count__gt=0,
        presence__last_seen__gte=cutoff,
    )


# Буквы алфавитного фильтра фамилий (кириллица без Ё/Й/Ъ/Ы/Ь + латиница A–Z).
_ADMIN_USERS_SURNAME_LETTERS = frozenset(
    'АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЮЯ'
    'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
)


def _parse_last_name_letter_param(request) -> str | None:
    """Одна буква фамилии (кириллица/латиница); Ё→Е, Й→И; иначе None."""
    raw = (request.query_params.get('letter') or '').strip()
    if not raw:
        return None
    letter = raw.upper()
    if letter == 'Ё':
        letter = 'Е'
    elif letter == 'Й':
        letter = 'И'
    if letter not in _ADMIN_USERS_SURNAME_LETTERS:
        return None
    return letter


def _last_name_letter_prefixes(letter: str) -> tuple[str, ...]:
    """Префиксы startswith с учётом регистра и групп Е/Ё, И/Й."""
    if letter == 'Е':
        return ('Е', 'е', 'Ё', 'ё')
    if letter == 'И':
        return ('И', 'и', 'Й', 'й')
    return (letter, letter.lower())


def _apply_last_name_letter_filter(queryset, letter: str | None):
    if not letter:
        return queryset
    clause = Q()
    for prefix in _last_name_letter_prefixes(letter):
        clause |= Q(last_name__startswith=prefix)
    return queryset.filter(clause)


def _get_admin_users_base_queryset(
    online_only=False,
    letter=None,
    presence=None,
    role_id=None,
    joined_from=None,
    joined_to=None,
    last_seen_from=None,
    last_seen_to=None,
):
    active_roles_qs = (
        UserRole.objects
        .filter(is_active=True)
        .select_related('role')
        .prefetch_related('role_groups')
    )

    users_qs = (
        User.objects
        .select_related('avatar')
        .prefetch_related(
            Prefetch('user_roles', queryset=active_roles_qs, to_attr='_active_roles')
        )
        .order_by('last_name', 'first_name', 'username')
    )

    presence_value = presence
    if presence_value is None and online_only:
        presence_value = 'online'
    if presence_value == 'online':
        users_qs = users_qs.filter(_online_presence_q())
    elif presence_value == 'offline':
        users_qs = users_qs.exclude(_online_presence_q())

    if role_id is not None:
        users_qs = users_qs.filter(
            user_roles__is_active=True,
            user_roles__role_id=role_id,
        )

    if joined_from is not None:
        users_qs = users_qs.filter(date_joined__gte=joined_from)
    if joined_to is not None:
        users_qs = users_qs.filter(date_joined__lte=joined_to)

    if last_seen_from is not None:
        users_qs = users_qs.filter(presence__last_seen__gte=last_seen_from)
    if last_seen_to is not None:
        users_qs = users_qs.filter(presence__last_seen__lte=last_seen_to)

    return _apply_last_name_letter_filter(users_qs, letter)


def _get_admin_users_queryset(search='', online_only=False, letter=None, **filters):
    """Обратная совместимость: queryset с фильтром поиска."""
    from src.core.cms.adp.services.user_search import apply_user_search

    return apply_user_search(
        _get_admin_users_base_queryset(
            online_only=online_only,
            letter=letter,
            **filters,
        ),
        search,
    )


def _build_admin_user_detail(user):
    admin_role = PermissionService._get_or_create_admin_role()
    user_role = _get_admin_user_role_for_display(user)
    role = PermissionService.resolve_display_role(
        user,
        user_role,
        admin_role=admin_role,
    )
    role_groups = list(user_role.role_groups.all()) if user_role else []

    data = CMSUserSerializer(user).data
    data['user_id'] = user.id
    data['public_id'] = str(user.public_id) if getattr(user, 'public_id', None) else None
    data['role'] = RoleSerializer(role).data if role else None
    data['role_groups'] = RoleGroupSerializer(role_groups, many=True).data
    data['avatar_url'] = _get_user_avatar_url(user)
    data['password_reset_mode'] = 'manual' if IS_DEVELOPMENT else 'system'
    return data
