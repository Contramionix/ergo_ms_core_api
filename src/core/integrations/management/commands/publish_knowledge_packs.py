"""Публикация пакетов справки этого процесса в media_api."""

from django.core.management.base import BaseCommand

from src.core.utils.knowledge_pack import publish_local_knowledge_packs, publish_owner_pack


class Command(BaseCommand):
    help = (
        'Пишет пакеты справки knowledge/<owner>/ на media_api этого процесса '
        '(ядро и локальные модули либо один вынесенный модуль).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--module',
            dest='module_name',
            default='',
            help='Опубликовать только этот модуль (на процессе модуля не пишет пакет ядра)',
        )

    def handle(self, *args, **options):
        module_name = (options.get('module_name') or '').strip()
        if module_name:
            item = publish_owner_pack(module_name)
            published = [item] if item else []
        else:
            published = publish_local_knowledge_packs()
        if not published:
            self.stdout.write(self.style.WARNING('Нет документов для публикации.'))
            return
        for item in published:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{item['owner']}: revision={item['revision']} path={item['media_path']}"
                )
            )
