"""Удаление устаревших записей UserDevice."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from src.core.cms.adp.models import UserDevice
from src.core.cms.adp.services.session_devices import revoke_user_device_session

logger = logging.getLogger('celery.core.cms.adp')

DEFAULT_BATCH_SIZE = 200


def purge_old_user_devices(
    *,
    retention_days: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
) -> int:
    """Удалить устройства с last_activity старше retention_days (с revoke сессии)."""
    days = retention_days if retention_days is not None else getattr(
        settings, 'API_SESSION_DEVICE_RETENTION_DAYS', 0
    )
    if days <= 0:
        logger.info(
            'session device purge: retention отключён (API_SESSION_DEVICE_RETENTION_DAYS=%s)',
            days,
        )
        return 0

    cutoff = timezone.now() - timedelta(days=days)
    batch_size = max(1, batch_size)
    qs = UserDevice.objects.filter(last_activity__lt=cutoff)

    if dry_run:
        count = qs.count()
        logger.info(
            'session device purge dry-run: %s устройств старше %s дней (до %s)',
            count,
            days,
            cutoff.isoformat(),
        )
        return count

    deleted_total = 0
    while True:
        devices = list(qs.order_by('id')[:batch_size])
        if not devices:
            break
        for device in devices:
            try:
                revoke_user_device_session(device)
                deleted_total += 1
            except Exception:
                logger.exception(
                    'session device purge: не удалось отозвать устройство id=%s',
                    getattr(device, 'id', None),
                )

    if deleted_total:
        logger.info(
            'session device purge: удалено %s устройств старше %s дней',
            deleted_total,
            days,
        )
    return deleted_total
