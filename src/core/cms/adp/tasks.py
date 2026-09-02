"""
Celery задачи для административной панели
"""
import logging
import pandas as pd
from celery import shared_task
from django.db import transaction
from django.contrib.auth import get_user_model

User = get_user_model()

from src.core.integrations import bridge
from src.core.integrations.module_contracts import CORE_BULK_USER_CREATE
from src.core.cms.adp.services.import_users_passwords import store_import_passwords
from src.core.cms.adp.services.import_users_welcome import (
    normalize_welcome_templates,
    render_welcome_email,
    send_import_welcome_email,
)
from src.core.cms.adp.services.registration import RegistrationService
from src.core.utils.methods import generate_secure_random_password

logger = logging.getLogger('celery.core.cms.adp')

MAX_LOGS_IN_META = 1000
PROGRESS_UPDATE_EVERY_ROWS = 10


@shared_task(name='core.cms.adp.purge_old_devices')
def purge_old_user_devices_task() -> int:
    """Периодическое удаление UserDevice по API_SESSION_DEVICE_RETENTION_DAYS."""
    from src.core.cms.adp.services.device_retention import purge_old_user_devices

    return purge_old_user_devices()



def _import_meta(current, total, created, skipped, progress, accumulated_logs, last_log, initiated_by_user_id=None):
    logs = accumulated_logs[-MAX_LOGS_IN_META:].copy()
    meta = {
        'current': current,
        'total': total,
        'created': created,
        'skipped': skipped,
        'progress': progress,
        'logs': logs,
        'logs_total': len(accumulated_logs),
        'last_log': last_log,
        'initiated_by_user_id': initiated_by_user_id,
    }
    return meta


def _normalize_cell(value):
    if pd.isna(value) or value == 'nan':
        return ''
    return str(value).strip()


def _load_existing_usernames() -> set[str]:
    return {
        username.lower()
        for username in User.objects.values_list('username', flat=True)
        if username
    }


def _load_existing_emails() -> set[str]:
    return {
        email.lower()
        for email in User.objects.exclude(email='').values_list('email', flat=True)
        if email
    }


def _should_update_progress(index: int, total_rows: int) -> bool:
    if index == 0 or index + 1 == total_rows:
        return True
    if (index + 1) % PROGRESS_UPDATE_EVERY_ROWS == 0:
        return True
    return False


def _append_skip(
    *,
    index,
    total_rows,
    message,
    results,
    accumulated_logs,
    task,
    initiated_by_user_id=None,
):
    logger.warning(message)
    log_entry = {'level': 'warn', 'message': message}
    results['logs'].append(log_entry)
    accumulated_logs.append(log_entry)
    results['skipped'] += 1
    progress = int((index + 1) / total_rows * 100)
    task.update_state(
        state='PROGRESS',
        meta=_import_meta(
            index + 1,
            total_rows,
            results['created'],
            results['skipped'],
            progress,
            accumulated_logs,
            log_entry,
            initiated_by_user_id,
        ),
    )


@shared_task(bind=True, name='core.cms.adp.import_users')
def import_users_task(
    self,
    file_content,
    file_name,
    initiated_by_user_id=None,
    send_welcome_emails=False,
    welcome_email_subject='',
    welcome_email_body='',
):
    """
    Celery задача для импорта пользователей из Excel/CSV файла.

    send_welcome_emails: отправлять приветственные письма пользователям с email.
    welcome_email_subject/body: шаблоны с плейсхолдерами {username}, {full_name}, ...
    """
    logger.info(f'Начало импорта пользователей из файла: {file_name}')

    try:
        from io import BytesIO
        file_io = BytesIO(file_content)

        if file_name.lower().endswith('.csv'):
            try:
                df = pd.read_csv(file_io, encoding='utf-8')
            except UnicodeDecodeError:
                file_io.seek(0)
                df = pd.read_csv(file_io, encoding='cp1251')
        else:
            df = pd.read_excel(file_io, header=0)

        df.columns = [str(col).strip().lower() for col in df.columns]

        column_mapping = {
            'фамилия': ['фамилия', 'last_name', 'lastname', 'surname'],
            'имя': ['имя', 'first_name', 'firstname', 'name'],
            'отчество': ['отчество', 'middle_name', 'middlename', 'patronymic'],
            'логин': ['логин', 'login', 'username', 'user'],
            'email': ['email', 'e-mail', 'почта', 'электронная почта', 'mail'],
        }

        found_columns = {}
        for target, variants in column_mapping.items():
            for col in df.columns:
                if col in variants:
                    found_columns[target] = col
                    break

        required = ['фамилия', 'имя', 'логин']
        missing = [col for col in required if col not in found_columns]
        if missing:
            error_msg = f'Отсутствуют обязательные колонки: {", ".join(missing)}'
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg,
                'created': 0,
                'skipped': 0,
                'total': 0,
                'initiated_by_user_id': initiated_by_user_id,
            }

        total_rows = len(df)
        results = {
            'created': 0,
            'skipped': 0,
            'errors': [],
            'logs': [],
            'emails_sent': 0,
            'emails_failed': 0,
            'emails_skipped_no_email': 0,
        }
        created_credentials = []
        pending_welcome_emails = []
        welcome_subject_template, welcome_body_template = normalize_welcome_templates(
            welcome_email_subject,
            welcome_email_body,
        )

        existing_usernames = _load_existing_usernames()
        check_email = RegistrationService.is_email_existence_check_enabled()
        existing_emails = _load_existing_emails() if check_email else set()

        logger.info(f'Файл прочитан. Всего строк: {total_rows}')

        accumulated_logs = []
        start_log = {'level': 'info', 'message': f'Начало импорта. Всего строк: {total_rows}'}
        accumulated_logs.append(start_log)
        self.update_state(
            state='PROGRESS',
            meta=_import_meta(0, total_rows, 0, 0, 0, accumulated_logs, start_log, initiated_by_user_id),
        )

        bridge.emit(CORE_BULK_USER_CREATE, phase='start')
        try:
            for index, row in df.iterrows():
                log_entry = None

                try:
                    last_name = _normalize_cell(row.get(found_columns['фамилия'], ''))
                    first_name = _normalize_cell(row.get(found_columns['имя'], ''))
                    middle_name = ''
                    if 'отчество' in found_columns:
                        middle_name = _normalize_cell(row.get(found_columns['отчество'], ''))

                    username = _normalize_cell(row.get(found_columns['логин'], ''))
                    email = ''
                    if 'email' in found_columns:
                        email = _normalize_cell(row.get(found_columns['email'], ''))

                    if not last_name or not first_name or not username:
                        _append_skip(
                            index=index,
                            total_rows=total_rows,
                            message=f'Строка {index + 2}: пропущена - пустые обязательные поля',
                            results=results,
                            accumulated_logs=accumulated_logs,
                            task=self,
                            initiated_by_user_id=initiated_by_user_id,
                        )
                        continue

                    username_key = username.lower()
                    if username_key in existing_usernames:
                        _append_skip(
                            index=index,
                            total_rows=total_rows,
                            message=f'Строка {index + 2}: логин "{username}" уже занят',
                            results=results,
                            accumulated_logs=accumulated_logs,
                            task=self,
                            initiated_by_user_id=initiated_by_user_id,
                        )
                        continue

                    email_key = email.lower() if email else ''
                    if check_email and email_key and email_key in existing_emails:
                        _append_skip(
                            index=index,
                            total_rows=total_rows,
                            message=(
                                f'Строка {index + 2}: Пользователь с таким email уже существует.'
                            ),
                            results=results,
                            accumulated_logs=accumulated_logs,
                            task=self,
                            initiated_by_user_id=initiated_by_user_id,
                        )
                        continue

                    with transaction.atomic():
                        user_password = generate_secure_random_password()
                        user = User.objects.create_user(
                            username=username,
                            first_name=first_name,
                            last_name=last_name,
                            middle_name=middle_name if middle_name else '',
                            email=email if email else '',
                            password=user_password,
                        )

                        created_credentials.append({
                            'last_name': last_name,
                            'first_name': first_name,
                            'middle_name': middle_name,
                            'username': username,
                            'email': email,
                            'password': user_password,
                        })

                        existing_usernames.add(username_key)
                        if email_key:
                            existing_emails.add(email_key)

                        if send_welcome_emails:
                            if email:
                                rendered_subject, rendered_body = render_welcome_email(
                                    user,
                                    subject_template=welcome_subject_template,
                                    body_template=welcome_body_template,
                                    password=user_password,
                                )
                                pending_welcome_emails.append({
                                    'row': index + 2,
                                    'username': username,
                                    'email': email,
                                    'subject': rendered_subject,
                                    'body': rendered_body,
                                })
                            else:
                                results['emails_skipped_no_email'] += 1

                        del user_password

                    results['created'] += 1
                    success_msg = (
                        f'Строка {index + 2}: создан "{last_name} {first_name}" ({username})'
                    )
                    logger.info('Создан пользователь ID=%s', user.id)
                    log_entry = {'level': 'success', 'message': success_msg}
                    results['logs'].append(log_entry)
                    accumulated_logs.append(log_entry)

                except Exception as e:
                    error_msg = f'Строка {index + 2}: ошибка - {str(e)}'
                    logger.error(error_msg, exc_info=True)
                    results['errors'].append(error_msg)
                    log_entry = {'level': 'error', 'message': error_msg}
                    results['logs'].append(log_entry)
                    accumulated_logs.append(log_entry)

                if _should_update_progress(index, total_rows):
                    progress = int((index + 1) / total_rows * 100)
                    self.update_state(
                        state='PROGRESS',
                        meta=_import_meta(
                            index + 1,
                            total_rows,
                            results['created'],
                            results['skipped'],
                            progress,
                            accumulated_logs,
                            log_entry,
                            initiated_by_user_id,
                        ),
                    )
        finally:
            bridge.emit(CORE_BULK_USER_CREATE, phase='end')

        if send_welcome_emails and pending_welcome_emails:
            for email_payload in pending_welcome_emails:
                email_sent, email_error = send_import_welcome_email(
                    email_payload['email'],
                    email_payload['subject'],
                    email_payload['body'],
                )
                if email_sent:
                    results['emails_sent'] += 1
                else:
                    results['emails_failed'] += 1
                    warn_msg = (
                        f'Строка {email_payload["row"]}: пользователь создан, '
                        f'но письмо не отправлено'
                    )
                    log_entry = {'level': 'warn', 'message': warn_msg}
                    results['logs'].append(log_entry)
                    accumulated_logs.append(log_entry)
                    if email_error:
                        logger.warning(
                            'Не удалось отправить приветственное письмо для %s: %s',
                            email_payload['username'],
                            email_error,
                        )

        logger.info(
            'Импорт завершен. Создано: %s, пропущено: %s',
            results['created'],
            results['skipped'],
        )

        final_log = {
            'level': 'success',
            'message': (
                f'Импорт завершён! Создано: {results["created"]}, '
                f'пропущено: {results["skipped"]}'
            ),
        }
        accumulated_logs.append(final_log)
        self.update_state(
            state='PROGRESS',
            meta=_import_meta(
                total_rows,
                total_rows,
                results['created'],
                results['skipped'],
                100,
                accumulated_logs,
                final_log,
                initiated_by_user_id,
            ),
        )

        passwords_available = bool(created_credentials)
        if passwords_available and initiated_by_user_id:
            store_import_passwords(self.request.id, initiated_by_user_id, created_credentials)

        return {
            'success': True,
            'created': results['created'],
            'skipped': results['skipped'],
            'total': total_rows,
            'errors': results['errors'],
            'logs': results['logs'],
            'passwords_available': passwords_available,
            'emails_sent': results['emails_sent'],
            'emails_failed': results['emails_failed'],
            'emails_skipped_no_email': results['emails_skipped_no_email'],
            'initiated_by_user_id': initiated_by_user_id,
        }

    except Exception as e:
        error_msg = f'Критическая ошибка при импорте: {str(e)}'
        logger.error(error_msg, exc_info=True)
        return {
            'success': False,
            'error': error_msg,
            'created': 0,
            'skipped': 0,
            'total': 0,
            'errors': [error_msg],
            'logs': [{'level': 'error', 'message': error_msg}],
        }
