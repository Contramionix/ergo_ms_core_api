"""Импорт пользователей (Celery)."""
import logging
import os

from django.http import HttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from src.core.utils.swagger.yasg_compat import swagger_auto_schema, openapi
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from src.core.cms.adp.services.import_users_passwords import (
    ImportPasswordsAccessError,
    build_passwords_excel,
    consume_import_passwords,
    is_passwords_download_available,
)
from src.core.cms.adp.services.import_users_welcome import (
    ImportWelcomeEmailError,
    get_welcome_email_defaults,
    normalize_welcome_templates,
    parse_send_welcome_emails_flag,
)
from src.core.cms.adp.services.permissions import PermissionService
from src.core.settings.views import _safe_content_disposition_filename
from src.core.utils.base.base_views import BaseAPIViewGlobalAdminMixin
from src.core.utils.mixins import MediaApiFileMixin, read_storage_file_bytes

logger = logging.getLogger(__name__)


class ImportUsersView(MediaApiFileMixin, BaseAPIViewGlobalAdminMixin):
    """
    Импорт пользователей из Excel или CSV файла через Celery с real-time прогрессом.
    Ожидаемые столбцы: Фамилия, Имя, Отчество, Логин, E-mail.
    Для каждого создаваемого пользователя генерируется случайный пароль;
    пароли доступны для одноразовой выгрузки в Excel после импорта.
    Проверка дубликатов по логину; по email — если включено REGISTRATION_CHECK_EMAIL_EXISTS.
    """
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @swagger_auto_schema(
        operation_description="Импорт пользователей из Excel (.xlsx, .xls) или CSV файла через Celery.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'file_path': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Путь к файлу, загруженному через media_api',
                ),
                'file': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    format=openapi.FORMAT_BINARY,
                    description='Файл Excel или CSV (multipart, альтернатива file_path)',
                ),
                'send_welcome_emails': openapi.Schema(
                    type=openapi.TYPE_BOOLEAN,
                    description='Отправлять приветственные письма (по умолчанию: false)',
                ),
                'welcome_email_subject': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Тема приветственного письма (шаблон с плейсхолдерами)',
                ),
                'welcome_email_body': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='Текст приветственного письма (шаблон с плейсхолдерами)',
                ),
            },
        ),
        responses={
            200: openapi.Response(
                description="Task ID для отслеживания прогресса.",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'task_id': openapi.Schema(type=openapi.TYPE_STRING),
                        'message': openapi.Schema(type=openapi.TYPE_STRING),
                    }
                )
            ),
            400: "Ошибка валидации."
        },
        security=[{'Bearer': []}]
    )
    def post(self, request):
        from src.core.cms.adp.tasks import import_users_task

        if 'send_welcome_emails' in request.data or 'send_welcome_emails' in request.POST:
            send_welcome_emails = parse_send_welcome_emails_flag(
                request.data.get('send_welcome_emails', request.POST.get('send_welcome_emails')),
            )
        else:
            send_welcome_emails = False

        welcome_email_subject = (
            request.data.get('welcome_email_subject')
            or request.POST.get('welcome_email_subject')
            or ''
        )
        welcome_email_body = (
            request.data.get('welcome_email_body')
            or request.POST.get('welcome_email_body')
            or ''
        )

        try:
            normalize_welcome_templates(welcome_email_subject, welcome_email_body)
        except ImportWelcomeEmailError as exc:
            return Response({'error': exc.message}, status=status.HTTP_400_BAD_REQUEST)

        logger.warning(
            'Запуск импорта пользователей через Celery. Пользователь: %s (ID: %s), send_welcome_emails: %s',
            request.user.username, request.user.id, send_welcome_emails,
        )

        file, file_path = self.get_file_or_path('file')
        if not file and not file_path:
            logger.warning('Попытка импорта без файла')
            return Response(
                {'error': _('Файл не найден')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if file:
            file_content = file.read()
            original_name = file.name
        else:
            file_content = read_storage_file_bytes(file_path)
            original_name = os.path.basename(file_path)

        file_name = original_name.lower()
        logger.warning('Получен файл для импорта: %s, размер: %s байт', original_name, len(file_content))

        if not file_name.endswith(('.xlsx', '.xls', '.csv')):
            logger.warning('Неподдерживаемый формат файла: %s', original_name)
            return Response(
                {'error': _('Поддерживаются только файлы Excel (.xlsx, .xls) и CSV (.csv)')},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            task = import_users_task.delay(
                file_content=file_content,
                file_name=original_name,
                initiated_by_user_id=request.user.id,
                send_welcome_emails=send_welcome_emails,
                welcome_email_subject=welcome_email_subject,
                welcome_email_body=welcome_email_body,
            )
            
            logger.warning(f'Celery задача запущена: task_id={task.id}')
            
            return Response({
                'task_id': task.id,
                'message': _('Импорт запущен. Используйте task_id для отслеживания прогресса.')
            }, status=status.HTTP_200_OK)
            
        except Exception:
            logger.error('Ошибка при запуске задачи импорта', exc_info=True)
            return Response({
                'error': _('Не удалось запустить импорт пользователей.')
            }, status=status.HTTP_400_BAD_REQUEST)


class ImportUsersTaskStatusView(BaseAPIViewGlobalAdminMixin):
    """
    Получение статуса Celery задачи импорта пользователей.

    Доступ ограничен глобальным администратором; дополнительно (С1) проверяется,
    что запрос либо от инициатора задачи, либо от администратора — на случай,
    если задачу запросят по чужому task_id.
    """
    
    @swagger_auto_schema(
        operation_description="Получить статус задачи импорта пользователей",
        manual_parameters=[
            openapi.Parameter(
                'task_id',
                openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                description='ID Celery задачи'
            )
        ],
        responses={
            200: openapi.Response(
                description="Статус задачи",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'state': openapi.Schema(type=openapi.TYPE_STRING),
                        'current': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'total': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'created': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'skipped': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'progress': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'result': openapi.Schema(type=openapi.TYPE_OBJECT),
                    }
                )
            )
        },
        security=[{'Bearer': []}]
    )
    def get(self, request, task_id):
        from celery.result import AsyncResult
        
        task = AsyncResult(task_id)

        initiated_by_user_id = None
        if task.state == 'PROGRESS' and isinstance(task.info, dict):
            initiated_by_user_id = task.info.get('initiated_by_user_id')
        elif task.state == 'SUCCESS' and isinstance(task.result, dict):
            initiated_by_user_id = task.result.get('initiated_by_user_id')

        if (
            initiated_by_user_id is not None
            and initiated_by_user_id != request.user.id
            and not PermissionService.can_manage_users_as_global_admin(request.user)
        ):
            return Response(
                {'error': _('Недостаточно прав для просмотра этой задачи.')},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Получаем индекс последнего лога для накопления
        last_log_index = int(request.query_params.get('last_log_index', 0))
        
        if task.state == 'PENDING':
            response = {
                'state': task.state,
                'current': 0,
                'total': 0,
                'created': 0,
                'skipped': 0,
                'progress': 0,
                'new_logs': [],
                'status': _('Задача в очереди...')
            }
        elif task.state == 'PROGRESS':
            all_logs = task.info.get('logs', [])
            logs_total = task.info.get('logs_total', len(all_logs))
            # Логи в meta — только последние N; считаем срез для клиента по last_log_index
            start = last_log_index - (logs_total - len(all_logs))
            if start >= len(all_logs):
                new_logs = []
            else:
                new_logs = all_logs[max(0, start):]
            if not new_logs and task.info.get('last_log') and last_log_index < logs_total:
                new_logs = [task.info.get('last_log')]
            response = {
                'state': task.state,
                'current': task.info.get('current', 0),
                'total': task.info.get('total', 0),
                'created': task.info.get('created', 0),
                'skipped': task.info.get('skipped', 0),
                'progress': task.info.get('progress', 0),
                'new_logs': new_logs,
                'logs_total': logs_total,
                'status': _('Обработка...')
            }
        elif task.state == 'SUCCESS':
            result = task.result or {}
            response = {
                'state': task.state,
                'current': result.get('total', 0),
                'total': result.get('total', 0),
                'created': result.get('created', 0),
                'skipped': result.get('skipped', 0),
                'progress': 100,
                'status': _('Завершено'),
                'result': result,
                'passwords_available': is_passwords_download_available(task_id, request.user),
            }
        elif task.state == 'FAILURE':
            response = {
                'state': task.state,
                'current': 0,
                'total': 0,
                'created': 0,
                'skipped': 0,
                'progress': 0,
                'status': _('Ошибка'),
                'error': str(task.info)
            }
        else:
            response = {
                'state': task.state,
                'current': 0,
                'total': 0,
                'created': 0,
                'skipped': 0,
                'progress': 0,
                'new_logs': [],
                'status': str(task.state)
            }
        
        return Response(response, status=status.HTTP_200_OK)


class ImportUsersWelcomeEmailDefaultsView(BaseAPIViewGlobalAdminMixin):
    """Шаблон приветственного письма для массового импорта пользователей."""

    @swagger_auto_schema(
        operation_description='Получить шаблон приветственного письма для импорта пользователей',
        responses={200: openapi.Response(description='Шаблон и список плейсхолдеров')},
        security=[{'Bearer': []}],
    )
    def get(self, request):
        return Response(get_welcome_email_defaults(), status=status.HTTP_200_OK)


class ImportUsersPasswordsDownloadView(BaseAPIViewGlobalAdminMixin):
    """Одноразовая выгрузка Excel с паролями импортированных пользователей."""

    @swagger_auto_schema(
        operation_description=(
            'Скачать Excel с паролями пользователей, созданных при импорте. '
            'Файл доступен только один раз.'
        ),
        manual_parameters=[
            openapi.Parameter(
                'task_id',
                openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                description='ID Celery задачи импорта',
            ),
        ],
        responses={
            200: 'Excel-файл с паролями',
            403: 'Недостаточно прав',
            410: 'Файл уже был скачан или недоступен',
        },
        security=[{'Bearer': []}],
    )
    def get(self, request, task_id):
        try:
            entries = consume_import_passwords(task_id, request.user)
        except ImportPasswordsAccessError as exc:
            return Response({'error': exc.message}, status=exc.status_code)

        excel_bytes = build_passwords_excel(entries)
        timestamp = timezone.now().strftime('%Y%m%d-%H%M')
        filename = _safe_content_disposition_filename(f'import-users-passwords-{timestamp}') + '.xlsx'
        response = HttpResponse(
            excel_bytes,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
