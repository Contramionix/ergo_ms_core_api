"""Celery-задачи индексации поиска."""

from __future__ import annotations

import logging

from celery import shared_task

from .registry import get_index
from .sync import delete_documents, ensure_registry_loaded, index_documents, reindex_index

logger = logging.getLogger('celery.core.search')


@shared_task(name='core.search.index_document', bind=True, max_retries=3, default_retry_delay=15)
def index_document_task(self, index_uid: str, document: dict) -> bool:
  try:
    ensure_registry_loaded()
    defn = get_index(index_uid)
    if not defn:
      return False
    index_documents(defn, [document])
    return True
  except Exception as exc:
    logger.exception('core.search.index_document: %s', index_uid)
    raise self.retry(exc=exc) from exc


@shared_task(name='core.search.delete_document')
def delete_document_task(index_uid: str, document_id: str) -> None:
  ensure_registry_loaded()
  delete_documents(index_uid, [str(document_id)])


@shared_task(name='core.search.reindex')
def reindex_task(index_uid: str | None = None) -> dict:
  ensure_registry_loaded()
  return reindex_index(index_uid)
