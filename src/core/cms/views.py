import logging

from rest_framework.response import Response
from rest_framework import status
from rest_framework.request import Request

from src.core.utils.swagger.yasg_compat import swagger_auto_schema, openapi

from django.contrib.auth import get_user_model

User = get_user_model()

from src.core.utils.base.base_views import BaseAPIViewAuthMixin
from src.core.cms.models import CMSPage, ApiEndpoint
from src.core.settings.models import UserAvatar
from src.core.cms.scripts import normalize_cms_path, sync_cms_pages
from src.core.cms.api_endpoints_sync import sync_api_endpoints

logger = logging.getLogger(__name__)


def _has_admin_panel_access(user) -> bool:
    from src.core.cms.adp.services.permissions import PermissionService

    return PermissionService.can_access_admin_panel(user)


class UserPublicInfoView(BaseAPIViewAuthMixin):
    """Публичные данные пользователя (имя, аватар).

    Резолвится только по public_id (UUID, непоследовательная ссылка) — без
    числового id, чтобы исключить перебор пользователей (enumeration).
    """

    @swagger_auto_schema(
        operation_description="Получение публичных данных пользователя по public_id",
        responses={
            200: "Публичные данные пользователя",
            401: "Пользователь не авторизован",
            404: "Пользователь не найден",
        },
    )
    def get(self, request: Request, ref):
        user = User.objects.select_related('avatar').filter(public_id=ref).first()
        if user is None:
            return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

        avatar_url = None
        try:
            avatar = getattr(user, 'avatar', None)
            if avatar and avatar.image:
                avatar_url = avatar.image.url
        except UserAvatar.DoesNotExist:
            avatar_url = None

        first_name = (user.first_name or '').strip()
        last_name = (user.last_name or '').strip()
        middle_name = (getattr(user, 'middle_name', '') or '').strip()
        full_name = user.get_full_name() if hasattr(user, 'get_full_name') else ''
        full_name = (full_name or '').strip() or user.username

        return Response({
            'public_id': str(user.public_id) if getattr(user, 'public_id', None) else None,
            'username': user.username,
            'first_name': first_name,
            'last_name': last_name,
            'middle_name': middle_name,
            'full_name': full_name,
            'avatar_url': avatar_url,
        }, status=status.HTTP_200_OK)


class SyncAllProjectPages(BaseAPIViewAuthMixin):
    @swagger_auto_schema(
        operation_description='Синхронизация путей CMS со всеми client-маршрутами',
        responses={
            200: "Пути получены и обновлены",
            401: "Пользователь не авторизован",
            403: "Нет доступа",
            400: "Ошибка чтения маршрутов",
        },
    )
    def post(self, request: Request):
        if not _has_admin_panel_access(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)

        try:
            from src.core.cms.client_routes_cache import invalidate_client_routes_index_cache

            invalidate_client_routes_index_cache()
            result = sync_cms_pages(remove_orphans=False)
            return Response(sorted(result.paths), status=status.HTTP_200_OK)
        except Exception:
            logger.exception('Ошибка синхронизации страниц CMS')
            return Response(
                {'error': 'Не удалось обновить страницы CMS.'},
                status=status.HTTP_400_BAD_REQUEST,
            )


class GetCMSPages(BaseAPIViewAuthMixin):
    @swagger_auto_schema(
        operation_description="Получение всех страниц CMS",
        responses={
            200: "Страницы получены",
            401: "Пользователь не авторизован",
            403: "Нет доступа",
        },
    )
    def get(self, request: Request):
        if not _has_admin_panel_access(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)

        from src.core.cms.client_routes_cache import get_client_routes_catalog
        from src.core.cms.models import PAGE_TYPE_WITHOUT_LIMITATIONS

        routes_catalog = get_client_routes_catalog()
        cms_pages_by_path = {
            normalize_cms_path(page.path.replace('\\\\', '\\')): page
            for page in CMSPage.objects.all()
        }

        pages_list = []
        for path in sorted(routes_catalog.keys()):
            route_meta = routes_catalog.get(path, {})
            cms_page = cms_pages_by_path.get(path)
            pages_list.append({
                'id': cms_page.id if cms_page else None,
                'path': path,
                'type': cms_page.page_type if cms_page else PAGE_TYPE_WITHOUT_LIMITATIONS,
                'module_name': route_meta.get('module_name', 'core'),
                'title': route_meta.get('title', ''),
                'title_key': route_meta.get('title_key', ''),
            })

        return Response({'pages': pages_list}, status=status.HTTP_200_OK)


class SyncApiEndpointsView(BaseAPIViewAuthMixin):
    @swagger_auto_schema(
        operation_description='Синхронизация каталога API-эндпоинтов с Django URLConf',
        responses={
            200: 'Эндпоинты синхронизированы',
            401: 'Пользователь не авторизован',
            403: 'Нет доступа',
            400: 'Ошибка синхронизации',
        },
    )
    def post(self, request: Request):
        if not _has_admin_panel_access(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)

        try:
            result = sync_api_endpoints(remove_orphans=False)
            return Response(
                {
                    'paths': sorted(result.paths),
                    'added': sorted(result.added),
                    'count': len(result.paths),
                },
                status=status.HTTP_200_OK,
            )
        except Exception:
            logger.exception('Ошибка синхронизации API-эндпоинтов')
            return Response(
                {'error': 'Не удалось синхронизировать API-эндпоинты.'},
                status=status.HTTP_400_BAD_REQUEST,
            )


class GetApiEndpointsView(BaseAPIViewAuthMixin):
    @swagger_auto_schema(
        operation_description='Каталог API-эндпоинтов для политик доступа',
        responses={
            200: 'Список эндпоинтов',
            401: 'Пользователь не авторизован',
            403: 'Нет доступа',
        },
    )
    def get(self, request: Request):
        if not _has_admin_panel_access(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)

        from src.core.cms.api_endpoints_sync import discover_api_endpoints

        discovered = discover_api_endpoints()
        db_by_path = {
            ep.path: ep
            for ep in ApiEndpoint.objects.all()
        }

        # Показываем discovery + записи из БД (после sync совпадают)
        all_paths = sorted(set(discovered.keys()) | set(db_by_path.keys()))
        endpoints = []
        for path in all_paths:
            meta = discovered.get(path) or {}
            db_ep = db_by_path.get(path)
            endpoints.append({
                'id': db_ep.id if db_ep else None,
                'path': path,
                'name': (db_ep.name if db_ep and db_ep.name else meta.get('name', '')) or '',
                'module_name': (
                    db_ep.module_name if db_ep and db_ep.module_name
                    else meta.get('module_name', 'core')
                ) or 'core',
                'title': (db_ep.name if db_ep and db_ep.name else meta.get('name', '')) or path,
            })

        from src.core.cms.adp.services.permission_catalog import get_modules_catalog

        return Response({
            'endpoints': endpoints,
            'modules': get_modules_catalog(),
        }, status=status.HTTP_200_OK)
