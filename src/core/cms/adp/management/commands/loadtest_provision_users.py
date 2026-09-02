"""
Создание / удаление эфемерных пользователей для ergoms loadtest.

Сообщения команды — на русском (management). JSON на диск — для CLI loadtest.
Поддерживает досоздание в существующий --run-id (--start-index).
"""

from __future__ import annotations

import json
import secrets
import uuid
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from src.core.cms.adp.models import UserDevice, UserProfile
from src.core.cms.adp.services.user_deletion import (
    UserDeletionBlockedError,
    delete_admin_user,
)
from src.core.cms.adp.session_context_tokens import create_scoped_session_tokens

User = get_user_model()

# Префикс логина: lt_<run_id>_<nnnnn> — только такие пользователи удаляются cleanup.
_USERNAME_PREFIX = 'lt_'


class Command(BaseCommand):
    help = (
        'Создаёт эфемерных пользователей loadtest с JWT (device-bound) '
        'или удаляет их (--cleanup; опционально по --run-id).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--count',
            type=int,
            default=0,
            help='Сколько пользователей создать (начиная с --start-index).',
        )
        parser.add_argument(
            '--start-index',
            type=int,
            default=1,
            help='Первый индекс пользователя в run-id (по умолчанию 1).',
        )
        parser.add_argument(
            '--run-id',
            type=str,
            default='',
            help=(
                'Идентификатор прогона (для cleanup ограничивает удаление; '
                'для provision генерируется).'
            ),
        )
        parser.add_argument(
            '--cleanup',
            action='store_true',
            help=(
                'Удалить пользователей loadtest: с --run-id — только прогон, '
                'без --run-id — всех с префиксом lt_.'
            ),
        )
        parser.add_argument(
            '--out',
            type=str,
            default='',
            help='Путь к JSON с токенами (режим provision).',
        )

    def handle(self, *args, **options):
        cleanup = bool(options['cleanup'])
        count = int(options['count'] or 0)
        start_index = int(options.get('start_index') or 1)
        run_id = (options.get('run_id') or '').strip()
        out_path = (options.get('out') or '').strip()

        if cleanup:
            if run_id:
                deleted = self._cleanup(run_id)
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Удалено пользователей loadtest: {deleted} (run-id={run_id})'
                    )
                )
            else:
                deleted = self._cleanup_all()
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Удалено пользователей loadtest: {deleted} (все lt_*)'
                    )
                )
            return

        if count < 1:
            raise CommandError('Укажите --count >= 1 для создания пользователей.')
        if start_index < 1:
            raise CommandError('Параметр --start-index должен быть >= 1.')
        if not out_path:
            raise CommandError('Укажите --out PATH для записи JSON с токенами.')

        if not run_id:
            run_id = uuid.uuid4().hex[:12]

        payload = self._provision(
            run_id=run_id,
            count=count,
            start_index=start_index,
        )
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        self.stdout.write(
            self.style.SUCCESS(
                f'Создано пользователей loadtest: {count} '
                f'(run-id={run_id}, start-index={start_index}) → {path}'
            )
        )

    def _provision(self, *, run_id: str, count: int, start_index: int) -> dict:
        users_out: list[dict] = []
        end_index = start_index + count - 1
        with transaction.atomic():
            for index in range(start_index, end_index + 1):
                username = f'{_USERNAME_PREFIX}{run_id}_{index:05d}'
                if User.objects.filter(username__iexact=username).exists():
                    raise CommandError(
                        f'Пользователь уже существует: {username}. '
                        f'Сначала выполните cleanup для run-id={run_id} '
                        f'или укажите другой --start-index.'
                    )
                password = secrets.token_urlsafe(24)
                user = User.objects.create_user(
                    username=username,
                    password=password,
                    email=f'{username}@loadtest.local',
                    first_name='Loadtest',
                    last_name=f'User{index:05d}',
                    is_active=True,
                    is_staff=False,
                    is_superuser=False,
                )
                UserProfile.objects.get_or_create(user=user)
                device = UserDevice.objects.create(
                    user=user,
                    device_type='desktop',
                    device_name=f'loadtest-{run_id}-{index:05d}',
                    ip_address='127.0.0.1',
                    user_agent='ergoms-loadtest/1.0',
                    is_active=True,
                )
                tokens = create_scoped_session_tokens(user, device=device)
                users_out.append(
                    {
                        'username': username,
                        'user_id': user.pk,
                        'index': index,
                        'access': tokens['access'],
                        'password': password,
                    }
                )
        return {
            'run_id': run_id,
            'count': count,
            'start_index': start_index,
            'end_index': end_index,
            'users': users_out,
            'access_tokens': [item['access'] for item in users_out],
        }

    def _delete_users(self, qs) -> int:
        total = qs.count()
        if total == 0:
            self.stdout.write('Пользователей loadtest для удаления: 0')
            return 0
        self.stdout.write(f'К удалению пользователей loadtest: {total}')
        self.stdout.flush()
        deleted = 0
        step = 1 if total <= 20 else 10
        for user in qs.iterator(chunk_size=50):
            try:
                delete_admin_user(user)
            except UserDeletionBlockedError as exc:
                raise CommandError(
                    f'Не удалось удалить {user.username}: {exc.detail or exc}'
                ) from exc
            deleted += 1
            if deleted == 1 or deleted == total or deleted % step == 0:
                self.stdout.write(f'Удалено: {deleted}/{total}')
                self.stdout.flush()
        return deleted

    def _cleanup(self, run_id: str) -> int:
        prefix = f'{_USERNAME_PREFIX}{run_id}_'
        qs = User.objects.filter(username__startswith=prefix).order_by('id')
        return self._delete_users(qs)

    def _cleanup_all(self) -> int:
        qs = User.objects.filter(username__startswith=_USERNAME_PREFIX).order_by('id')
        return self._delete_users(qs)
