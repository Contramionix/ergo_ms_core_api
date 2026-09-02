import os
import re
import json
import logging
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework.response import Response
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.http import HttpResponse

from .permissions import IsGlobalAdmin
from src.core.utils.mixins import MediaApiFileMixin, read_storage_file_bytes, SwaggerSafeMixin
from src.core.utils.permissions import ObjectPermissionMixin
from src.core.audit.mixin import AuditedModelMixin

from .models import *
from .serializers import *


logger = logging.getLogger(__name__)


def _safe_content_disposition_filename(name):
    """Санитизирует имя файла для заголовка Content-Disposition (защита от response splitting)."""
    if name is None:
        return 'download'
    s = re.sub(r'[\x00-\x1f\x7f"\\\r\n]', '', str(name).strip())
    s = s[:200] if len(s) > 200 else s
    return s or 'download'


class _ThemeImportMixin(MediaApiFileMixin):
    """Чтение JSON-темы из прямой загрузки или из пути media_api."""

    def _read_theme_json(self, request):
        file, file_path = self.get_file_or_path('file')
        if file_path:
            return read_storage_file_bytes(file_path).decode('utf-8')
        if file:
            return file.read().decode('utf-8')
        return None


class _SettingsAuditMixin(AuditedModelMixin):
    """Изменения настроек-синглтонов пишутся единым действием settings.changed."""
    audit_module = 'core.settings'
    audit_severity = 'security'
    audit_action_map = {'create': 'settings.changed', 'update': 'settings.changed'}


class EmailSettingsViewSet(SwaggerSafeMixin, _SettingsAuditMixin, viewsets.ModelViewSet):
    queryset = EmailSettings.objects.all()
    serializer_class = EmailSettingsSerializer
    permission_classes = [IsAuthenticated, IsGlobalAdmin]

    def get_queryset(self):
        return self.get_safe_queryset(EmailSettings.objects.all())

class UserAvatarViewSet(ObjectPermissionMixin, SwaggerSafeMixin, MediaApiFileMixin, viewsets.ModelViewSet):
    queryset = UserAvatar.objects.all()
    serializer_class = UserAvatarSerializer
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        """Используем легковесный сериализатор для списка"""
        if self.action == 'list':
            from .serializers import UserAvatarListSerializer
            return UserAvatarListSerializer
        return UserAvatarSerializer
    
    def get_queryset(self):
        if self.is_swagger_fake_view():
            return UserAvatar.objects.none()
        if not self.request.user.is_authenticated:
            return UserAvatar.objects.none()
        return UserAvatar.objects.filter(user=self.request.user)

    def check_object_permission(self, request, obj):
        return getattr(obj, 'user_id', None) == getattr(request.user, 'pk', None)
    
    def perform_create(self, serializer):
        if self.request.user.is_authenticated:
            UserAvatar.objects.filter(user=self.request.user).delete()
            if self.request.data.get('image_path') or self.request.FILES.get('image'):
                self.get_file_or_path('image')
            serializer.save(user=self.request.user)
    
    @action(detail=False, methods=['delete'], url_path='current')
    def delete_current(self, request):
        """Удалить аватар текущего пользователя"""
        if not request.user.is_authenticated:
            return Response(
                {'error': 'Пользователь не аутентифицирован'},
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        avatar = UserAvatar.objects.filter(user=request.user).first()
        if avatar:
            avatar.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(
            {'detail': 'Аватар не найден'},
            status=status.HTTP_404_NOT_FOUND
        )

class ThemeViewSet(SwaggerSafeMixin, AuditedModelMixin, _ThemeImportMixin, viewsets.ModelViewSet):
    """ViewSet для управления темами оформления"""
    queryset = Theme.objects.all()
    serializer_class = ThemeSerializer
    audit_module = 'core.settings'
    audit_entity_type = 'theme'
    audit_action_map = {'create': 'theme.created', 'update': 'theme.updated', 'destroy': 'theme.deleted'}

    def get_permissions(self):
        if self.action == 'get_active_theme':
            return [AllowAny()]
        if self.action in (
            'catalog',
            'my_preference',
            'select_my_theme',
            'update_my_favorites',
        ):
            return [IsAuthenticated()]
        if self.action in ('list', 'retrieve'):
            # Полный список — редактор админа; пользователям — catalog/
            return [IsAuthenticated(), IsGlobalAdmin()]
        return [IsAuthenticated(), IsGlobalAdmin()]
    
    def get_queryset(self):
        if self.is_swagger_fake_view():
            return Theme.objects.none()
        queryset = Theme.objects.all()
        module_key = self.request.query_params.get('module')
        if module_key == 'site' or module_key == '':
            queryset = queryset.filter(module_key__isnull=True)
        elif module_key:
            queryset = queryset.filter(module_key=module_key)
        as_pairs = self.request.query_params.get('as_pairs')
        if module_key and module_key not in ('site', '') and as_pairs == 'true':
            return queryset
        base_theme = self.request.query_params.get('base_theme')
        if base_theme:
            queryset = queryset.filter(base_theme=base_theme)
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active == 'true')
        return queryset
    
    def destroy(self, request, *args, **kwargs):
        """Запрет удаления системных тем и стандарта сайта"""
        instance = self.get_object()
        if instance.is_system:
            return Response(
                {'error': 'Нельзя удалить системную тему'},
                status=status.HTTP_403_FORBIDDEN
            )
        if not instance.module_key and instance.is_default:
            return Response(
                {'error': 'Нельзя удалить стандарт системы. Сначала назначьте другой стандарт.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().destroy(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        module_key = request.query_params.get('module')
        if module_key and module_key not in ('site', '') and request.query_params.get('as_pairs') == 'true':
            from src.core.settings.services.module_theme_sets import list_module_pairs

            pairs = list_module_pairs(self.get_queryset(), module_key)
            return Response(pairs)
        return super().list(request, *args, **kwargs)
    
    @action(detail=False, methods=['get'], url_path='active')
    def get_active_theme(self, request):
        """Эффективная тема: личная для авторизованного или стандарт сайта; для модуля — пара."""
        module_key = request.query_params.get('module')
        if module_key and module_key not in ('site', ''):
            from src.core.settings.services.module_theme_sets import build_module_theme_set

            built = build_module_theme_set(Theme.objects.all(), module_key)
            if built:
                return Response(built)
            return Response(
                {'detail': 'Активная тема модуля не найдена'},
                status=status.HTTP_404_NOT_FOUND,
            )

        from src.core.settings.services.site_theme_pairs import build_site_theme_pair
        from src.core.settings.services.user_theme_preference import get_effective_site_theme

        user = request.user if getattr(request.user, 'is_authenticated', False) else None
        effective = get_effective_site_theme(user)
        if effective:
            pair = build_site_theme_pair(Theme.objects.filter(module_key__isnull=True), effective)
            return Response(pair)
        return Response({'detail': _('Активная тема не найдена')}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=['get'], url_path='catalog')
    def catalog(self, request):
        """Темы сайта, доступные в общем быстром выборе (сгруппированы в пары light/dark)."""
        from src.core.settings.services.site_theme_pairs import list_site_theme_pairs

        qs = Theme.objects.filter(module_key__isnull=True, is_available=True)
        return Response(list_site_theme_pairs(qs))

    @action(detail=False, methods=['get', 'patch'], url_path='me')
    def my_preference(self, request):
        """Личные предпочтения темы текущего пользователя."""
        from src.core.settings.services.user_theme_preference import (
            preference_payload,
            select_user_theme,
        )

        if request.method == 'GET':
            return Response(preference_payload(request.user))

        if 'selected_theme_id' not in request.data:
            return Response(preference_payload(request.user))
        try:
            payload = select_user_theme(request.user, request.data.get('selected_theme_id'))
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)

    @action(detail=False, methods=['post'], url_path='me/select')
    def select_my_theme(self, request):
        """Выбрать личную палитру или сбросить к стандарту сайта (null)."""
        from src.core.settings.services.user_theme_preference import select_user_theme

        if 'theme_id' not in request.data and 'selected_theme_id' not in request.data:
            return Response(
                {'error': 'Укажите theme_id или selected_theme_id'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        theme_id = request.data.get('theme_id', request.data.get('selected_theme_id'))
        try:
            payload = select_user_theme(request.user, theme_id)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)

    @action(detail=False, methods=['post'], url_path='me/favorites')
    def update_my_favorites(self, request):
        """Добавить или убрать тему из личного быстрого списка."""
        from src.core.settings.services.user_theme_preference import update_user_favorites

        add_id = request.data.get('add')
        remove_id = request.data.get('remove')
        if add_id is None and remove_id is None:
            return Response(
                {'error': 'Укажите add и/или remove'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            payload = update_user_favorites(request.user, add_id=add_id, remove_id=remove_id)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)

    @action(detail=True, methods=['post'], url_path='set-default')
    def set_default_theme(self, request, pk=None):
        """Назначить тему стандартом сайта (is_default + is_active + is_available)."""
        from src.core.settings.services.user_theme_preference import set_site_default_theme

        theme = self.get_object()
        if theme.module_key:
            return Response(
                {'error': 'Стандарт системы задаётся только для тем системы'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            theme = set_site_default_theme(theme)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ThemeSerializer(theme).data)

    @action(detail=True, methods=['post'], url_path='activate')
    def activate_theme(self, request, pk=None):
        """Активировать пару модуля; для сайта — синоним set-default."""
        theme = self.get_object()
        if theme.module_key:
            from src.core.settings.services.module_theme_sets import activate_module_pair

            built = activate_module_pair(Theme, theme.module_key, theme.module_pair or 'default')
            if not built:
                return Response(
                    {'error': 'Не удалось активировать пару модульных тем'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(built)
        from src.core.settings.services.user_theme_preference import set_site_default_theme

        theme = set_site_default_theme(theme)
        return Response(ThemeSerializer(theme).data)
    
    @action(detail=True, methods=['post'], url_path='duplicate')
    def duplicate_theme(self, request, pk=None):
        """Создать копию темы"""
        source_theme = self.get_object()
        new_name = request.data.get('name', f'{source_theme.name} (копия)')
        
        new_theme = Theme.objects.create(
            name=new_name,
            description=source_theme.description,
            author=request.data.get('author', source_theme.author),
            base_theme=source_theme.base_theme,
            module_key=source_theme.module_key,
            module_pair=source_theme.module_pair or 'default',
            colors=source_theme.colors.copy(),
            bootstrap_colors=source_theme.bootstrap_colors.copy() if source_theme.bootstrap_colors else {},
            module_tokens=source_theme.module_tokens.copy() if source_theme.module_tokens else {},
            is_active=False,
            is_default=False,
            is_system=False
        )
        return Response(ThemeSerializer(new_theme).data, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['get'], url_path='export')
    def export_theme(self, request, pk=None):
        """Экспорт темы в JSON файл"""
        theme = self.get_object()
        
        export_data = {
            'name': theme.name,
            'description': theme.description,
            'author': theme.author,
            'base_theme': theme.base_theme,
            'module_key': theme.module_key,
            'module_pair': theme.module_pair,
            'colors': theme.colors,
            'bootstrap_colors': theme.bootstrap_colors,
            'module_tokens': theme.module_tokens,
            'version': '1.1',
            'exported_at': str(timezone.now())
        }
        
        response = HttpResponse(
            json.dumps(export_data, indent=2, ensure_ascii=False),
            content_type='application/json; charset=utf-8'
        )
        safe_name = _safe_content_disposition_filename(theme.name.replace(' ', '-').lower()) + '.json'
        response['Content-Disposition'] = f'attachment; filename="{safe_name}"'
        return response
    
    @action(detail=False, methods=['post'], url_path='import')
    def import_theme(self, request):
        """Импорт темы из JSON файла"""
        try:
            file_content = self._read_theme_json(request)
            if file_content is None:
                return Response(
                    {'error': 'Файл не передан'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            theme_data = json.loads(file_content)
            
            # Валидация обязательных полей
            required_fields = ['name', 'base_theme', 'colors']
            for field in required_fields:
                if field not in theme_data:
                    return Response(
                        {'error': f'Отсутствует обязательное поле: {field}'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            # Создаем новую тему
            new_theme = Theme.objects.create(
                name=theme_data['name'],
                description=theme_data.get('description', ''),
                author=theme_data.get('author', ''),
                base_theme=theme_data['base_theme'],
                module_key=theme_data.get('module_key'),
                module_pair=theme_data.get('module_pair') or 'default',
                colors=theme_data['colors'],
                bootstrap_colors=theme_data.get('bootstrap_colors', {}),
                module_tokens=theme_data.get('module_tokens', {}),
                is_active=False,
                is_default=False,
                is_system=False
            )
            
            return Response(
                ThemeSerializer(new_theme).data,
                status=status.HTTP_201_CREATED
            )
        except json.JSONDecodeError:
            return Response(
                {'error': 'Неверный формат JSON'},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception:
            logger.exception('Ошибка импорта темы')
            return Response(
                {'error': 'Не удалось импортировать тему.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
    
    @action(detail=True, methods=['post'], url_path='reset-defaults')
    def reset_defaults(self, request, pk=None):
        """Сбросить системную тему к начальным значениям"""
        from src.core.settings.services.theme_seed import (
            reset_system_theme_to_defaults,
            reset_module_theme_from_snapshot,
            ensure_module_themes_from_manifests,
        )

        theme = self.get_object()
        if not theme.is_system:
            return Response(
                {'error': 'Сброс доступен только для системных тем'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if theme.module_key:
            manifest = request.data.get('manifest')
            if manifest:
                ensure_module_themes_from_manifests(Theme, [manifest], update_existing=True)
                theme.refresh_from_db()
                return Response(ThemeSerializer(theme).data)
            if not reset_module_theme_from_snapshot(theme):
                return Response(
                    {
                        'error': (
                            'Не удалось сбросить модульную тему: нет снимка начальных значений. '
                            'Выполните синхронизацию модульных тем в редакторе тем.'
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            theme.refresh_from_db()
            return Response(ThemeSerializer(theme).data)

        if not reset_system_theme_to_defaults(theme):
            return Response(
                {'error': 'Не удалось определить начальные значения темы'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        theme.refresh_from_db()
        return Response(ThemeSerializer(theme).data)

    @action(detail=False, methods=['post'], url_path='create-system-themes')
    def create_system_themes(self, request):
        """Создать или обновить системные темы (light и dark)"""
        from src.core.settings.services.theme_seed import ensure_system_themes

        created, updated = ensure_system_themes(Theme, update_existing=True)

        message = []
        if created:
            message.append(f'Создано {len(created)} тем')
        if updated:
            message.append(f'Обновлено {len(updated)} тем')

        return Response({
            'message': ', '.join(message) if message else 'Темы актуальны',
            'created': [ThemeSerializer(theme).data for theme in created],
            'updated': [ThemeSerializer(theme).data for theme in updated],
        })

    @action(detail=False, methods=['post'], url_path='sync-module-defaults')
    def sync_module_defaults(self, request):
        """Создать или обновить системные темы модулей из manifest (тело запроса)."""
        from src.core.settings.services.theme_seed import ensure_module_themes_from_manifests

        manifests = request.data.get('manifests')
        if not manifests or not isinstance(manifests, list):
            return Response(
                {'error': 'Передайте массив manifests в теле запроса'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        created, updated = ensure_module_themes_from_manifests(Theme, manifests, update_existing=True)

        message = []
        if created:
            message.append(f'Создано {len(created)} модульных тем')
        if updated:
            message.append(f'Обновлено {len(updated)} модульных тем')

        return Response({
            'message': ', '.join(message) if message else 'Модульные темы актуальны',
            'created': [ThemeSerializer(theme).data for theme in created],
            'updated': [ThemeSerializer(theme).data for theme in updated],
        })