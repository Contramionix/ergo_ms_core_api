"""Общие миксины моделей API."""

import uuid

from django.db import models


class PublicIdMixin(models.Model):
    """Внешний идентификатор UUID. В URL и клиенте — public_id, не pk БД."""

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
    )

    class Meta:
        abstract = True
