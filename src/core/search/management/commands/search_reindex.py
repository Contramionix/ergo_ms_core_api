"""Management-команда полной переиндексации Meilisearch."""

from django.core.management.base import BaseCommand

from src.core.search.sync import reindex_index


class Command(BaseCommand):
  help = 'Переиндексация документов в Meilisearch (все индексы или --index=uid).'

  def add_arguments(self, parser):
    parser.add_argument(
      '--index',
      dest='index_uid',
      default=None,
      help='UID индекса (например core_users); без аргумента — все индексы',
    )

  def handle(self, *args, **options):
    stats = reindex_index(options.get('index_uid'))
    if not stats:
      self.stdout.write(self.style.WARNING('Индексы не обновлены (Meilisearch недоступен или индекс не найден).'))
      return
    for uid, count in stats.items():
      self.stdout.write(self.style.SUCCESS(f'{uid}: {count} документов'))
