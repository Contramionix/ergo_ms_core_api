# -*- coding: utf-8 -*-
"""Удаление записей UserDevice старше заданного срока."""

from django.conf import settings
from django.core.management.base import BaseCommand

from src.core.cms.adp.services.device_retention import purge_old_user_devices


class Command(BaseCommand):
    help = (
        'Удаляет записи устройств старше API_SESSION_DEVICE_RETENTION_DAYS '
        '(или --days). Перед удалением отзывает сессию. При --dry-run только считает.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=None,
            help='Срок хранения в днях (переопределяет API_SESSION_DEVICE_RETENTION_DAYS)',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=200,
            help='Размер пакета удаления (по умолчанию 200)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Подсчитать записи без удаления',
        )

    def handle(self, *args, **options):
        days = options['days']
        if days is None:
            days = getattr(settings, 'API_SESSION_DEVICE_RETENTION_DAYS', 0)

        if days <= 0 and not options['dry_run']:
            self.stderr.write(
                self.style.WARNING(
                    'API_SESSION_DEVICE_RETENTION_DAYS=0 — укажите --days или задайте переменную в .env'
                )
            )
            return

        count = purge_old_user_devices(
            retention_days=days,
            batch_size=options['batch_size'],
            dry_run=options['dry_run'],
        )

        if options['dry_run']:
            self.stdout.write(f'Будет удалено устройств: {count}')
        else:
            self.stdout.write(self.style.SUCCESS(f'Удалено устройств: {count}'))
