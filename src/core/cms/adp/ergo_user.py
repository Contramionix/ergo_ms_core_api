"""
Кастомная модель пользователя ERGO MS.

Физическая таблица — auth_user (совместимость с существующими FK и данными).
"""

from __future__ import annotations

import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


class ErgoUser(AbstractUser):
    middle_name = models.CharField(
        max_length=150,
        blank=True,
        default='',
        verbose_name='Отчество',
    )
    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        null=True,
        editable=False,
        verbose_name='public id',
    )

    class Meta:
        db_table = 'auth_user'
        verbose_name = 'пользователь'
        verbose_name_plural = 'пользователи'
        swappable = 'AUTH_USER_MODEL'

    def get_full_name(self) -> str:
        """Формат: «Фамилия Имя Отчество»."""
        name_parts = []
        last_name = (self.last_name or '').strip()
        if last_name:
            name_parts.append(last_name)
        first_name = (self.first_name or '').strip()
        if first_name:
            name_parts.append(first_name)
        middle_name = (self.middle_name or '').strip()
        if middle_name:
            name_parts.append(middle_name)

        full_name = ' '.join(part for part in name_parts if part and str(part).strip())
        return full_name.strip() or self.username or self.email

    def get_initials_name(self) -> str:
        """Формат: «Фамилия И.О.»."""
        last_name = (self.last_name or '').strip()
        first_name = (self.first_name or '').strip()
        middle_name = (self.middle_name or '').strip()

        initials_parts = []
        if first_name:
            initials_parts.append(f'{first_name[0].upper()}.')
        if middle_name:
            initials_parts.append(f'{middle_name[0].upper()}.')

        initials_block = ''.join(initials_parts)

        if last_name:
            if initials_block:
                return f'{last_name} {initials_block}'
            return last_name

        return initials_block or self.username
