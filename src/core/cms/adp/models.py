from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext as _
import json

from src.core.cms.adp.ergo_user import ErgoUser  # noqa: F401


class EmailConfirmationCode(models.Model):
    email = models.EmailField(unique=True)
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    failed_attempts = models.PositiveSmallIntegerField(default=0)

    def __str__(self):
        return f"{self.email} - {self.code}"


def _default_user_language():
    """Язык нового профиля = DEFAULT_LANGUAGE из .env (settings.LANGUAGE_CODE)."""
    code = (getattr(settings, 'LANGUAGE_CODE', None) or 'ru').strip().lower()
    return code.split('-', 1)[0][:10] or 'ru'


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='adp_profile')
    phone = models.CharField(max_length=20, blank=True, null=True)
    bio = models.TextField(max_length=500, blank=True, null=True)
    language = models.CharField(
        max_length=10,
        default=_default_user_language,
        choices=[('ru', 'Русский'), ('en', 'English'), ('fr', 'Français')],
    )
    timezone = models.CharField(max_length=50, default='Europe/Moscow')
    
    # Настройки уведомлений (каналы email — через notifications preferences)
    push_notifications = models.BooleanField(default=True)
    sms_notifications = models.BooleanField(default=False)
    
    # Настройки приватности
    profile_visibility = models.CharField(
        max_length=10,
        choices=[
            ('public', 'Публичный'),
            ('private', 'Приватный'),
            ('friends', 'Только друзья'),
        ],
        default='public'
    )
    
    # Двухфакторная аутентификация
    two_factor_enabled = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Профиль {self.user.username}"
    
    @property
    def full_name(self):
        """Формат: Фамилия Имя Отчество"""
        name_parts = []
        if self.user.last_name:
            name_parts.append(self.user.last_name)
        if self.user.first_name:
            name_parts.append(self.user.first_name)
        if self.user.middle_name:
            name_parts.append(self.user.middle_name)
        full_name = " ".join(part for part in name_parts if part and part.strip())
        return full_name or self.user.username


class UserDevice(models.Model):
    DEVICE_TYPES = [
        ('desktop', 'Desktop'),
        ('laptop', 'Laptop'),
        ('mobile', 'Mobile'),
        ('tablet', 'Tablet'),
    ]
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='devices')
    device_type = models.CharField(max_length=20, choices=DEVICE_TYPES)
    device_name = models.CharField(max_length=100)
    ip_address = models.GenericIPAddressField()
    user_agent = models.TextField()
    city = models.CharField(max_length=100, blank=True, null=True)
    country = models.CharField(max_length=100, blank=True, null=True)
    is_active = models.BooleanField(default=False)
    outstanding_token_jti = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        db_index=True,
        verbose_name='JTI refresh-токена',
    )
    last_activity = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['user', 'device_name', 'ip_address']
    
    def __str__(self):
        return f"{self.user.username} - {self.device_name}"


class UserPresence(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='presence',
        verbose_name='Пользователь',
    )
    connection_count = models.PositiveIntegerField(default=0, verbose_name='Число WS-подключений')
    last_seen = models.DateTimeField(null=True, blank=True, verbose_name='Последняя активность')

    class Meta:
        app_label = 'cms_adp'
        verbose_name = 'Онлайн-статус пользователя'
        verbose_name_plural = 'Онлайн-статусы пользователей'

    def __str__(self):
        return f"{self.user.username} ({'online' if self.is_online else 'offline'})"

    @property
    def is_online(self):
        from src.core.cms.adp.services.presence import effective_is_online

        return effective_is_online(self)


class Role(Group):
    """
    Роли в системе.
    """

    ROLE_TYPE_LABELS = {
        True: 'Администратор',
        False: 'Пользователь',
    }
    
    description = models.TextField(blank=True, null=True, verbose_name='Описание')
    is_system = models.BooleanField(default=False, verbose_name='Системная роль')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        app_label = 'cms_adp'
        verbose_name = 'Роль'
        verbose_name_plural = 'Роли'
        ordering = ['name']
    
    def __str__(self):
        return f"{self.name} ({self.get_role_type_display()})"
    
    def clean(self):
        # Системные роли нельзя переводить в пользовательские
        if self.pk:
            original = Role.objects.get(pk=self.pk)
            if original.is_system and not self.is_system:
                raise ValidationError(_('Нельзя отключить системный статус роли'))

    @property
    def role_type(self) -> str:
        """Определяет тип роли: admin или user"""
        # Для системных ролей определяем тип по имени
        if self.is_system:
            if self.name == 'Администратор':
                return 'admin'
            elif self.name == 'Пользователь':
                return 'user'
            # Для других системных ролей определяем по is_system (по умолчанию admin)
            return 'admin'
        # Для несистемных ролей тип user
        return 'user'

    def get_role_type_display(self) -> str:
        """Возвращает отображаемое название типа роли"""
        role_type = self.role_type
        if role_type == 'admin':
            return _('Администратор')
        elif role_type == 'user':
            return _('Пользователь')
        return _('Неизвестная роль')


class RoleGroup(models.Model):
    """
    Дочерние ролевые группы для пользователей.
    Права настраиваются отдельно в каждом модуле системы.
    """
    name = models.CharField(max_length=100, verbose_name='Название группы')
    parent_role = models.ForeignKey(
        Role, 
        on_delete=models.CASCADE, 
        related_name='role_groups',
        limit_choices_to={'is_system': False},
        verbose_name='Родительская роль'
    )
    description = models.TextField(blank=True, null=True, verbose_name='Описание')
    is_active = models.BooleanField(default=True, verbose_name='Активна')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Ролевая группа'
        verbose_name_plural = 'Ролевые группы'
        ordering = ['name']
        unique_together = ['name', 'parent_role']
    
    def __str__(self):
        return f"{self.name} (группа роли {self.parent_role.name})"


class Policy(models.Model):
    """
    Политики доступа к URL-адресам системы.
    Администраторы имеют доступ ко всем URL.
    Для пользователей доступ настраивается индивидуально.
    """
    POLICY_TYPES = [
        ('url', 'Доступ к URL'),
        ('api', 'Доступ к API'),
    ]
    
    ACTION_TYPES = [
        ('allow', 'Разрешить'),
        ('deny', 'Запретить'),
    ]
    
    name = models.CharField(max_length=200, verbose_name='Название политики')
    policy_type = models.CharField(max_length=20, choices=POLICY_TYPES, default='url', verbose_name='Тип политики')
    action = models.CharField(max_length=10, choices=ACTION_TYPES, default='allow', verbose_name='Действие')
    
    # URL страницы или API path
    resource_path = models.CharField(max_length=500, verbose_name='Путь к ресурсу')
    
    # Поддержка wildcards для URL (например, /api/users/*)
    is_pattern = models.BooleanField(default=False, verbose_name='Использовать шаблон')
    
    # Связь с ролями и группами
    role = models.ForeignKey(
        Role, 
        on_delete=models.CASCADE, 
        related_name='policies',
        blank=True,
        null=True,
        verbose_name='Роль'
    )
    role_group = models.ForeignKey(
        RoleGroup, 
        on_delete=models.CASCADE, 
        related_name='policies',
        blank=True,
        null=True,
        verbose_name='Ролевая группа'
    )
    
    description = models.TextField(blank=True, null=True, verbose_name='Описание')
    is_active = models.BooleanField(default=True, verbose_name='Активна')
    priority = models.IntegerField(default=0, verbose_name='Приоритет')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Политика'
        verbose_name_plural = 'Политики'
        ordering = ['-priority', 'name']
    
    def __str__(self):
        target = self.role.name if self.role else self.role_group.name
        return f"{self.name} ({target}): {self.action} {self.resource_path}"
    
    def clean(self):
        # Политика должна быть привязана либо к роли, либо к группе
        if not self.role and not self.role_group:
            raise ValidationError(_('Политика должна быть привязана к роли или ролевой группе'))
        if self.role and self.role_group:
            raise ValidationError(_('Политика не может быть одновременно привязана к роли и ролевой группе'))


class UserRole(models.Model):
    """
    Связь пользователей с ролями и группами.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='user_roles', verbose_name='Пользователь')
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name='user_assignments', verbose_name='Роль')
    role_groups = models.ManyToManyField(
        RoleGroup, 
        blank=True, 
        related_name='user_assignments',
        verbose_name='Ролевые группы'
    )
    
    is_active = models.BooleanField(default=True, verbose_name='Активна')
    assigned_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата назначения')
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='role_assignments_made',
        verbose_name='Назначил'
    )
    
    class Meta:
        verbose_name = 'Роль пользователя'
        verbose_name_plural = 'Роли пользователей'
        unique_together = ['user', 'role']
    
    def __str__(self):
        return f"{self.user.username} - {self.role.name}"
    
    def clean(self):
        # Пользователь может иметь только одну активную роль
        if self.is_active:
            existing = UserRole.objects.filter(
                user=self.user, 
                is_active=True
            ).exclude(pk=self.pk)
            if existing.exists():
                raise ValidationError(_('У пользователя уже есть активная роль'))


class ModulePermission(models.Model):
    """
    Права доступа к функционалу модулей системы.
    Настраиваются для ролевых групп.
    """
    module_name = models.CharField(max_length=100, verbose_name='Название модуля')
    permission_key = models.CharField(max_length=100, verbose_name='Ключ разрешения')
    permission_name = models.CharField(max_length=200, verbose_name='Название разрешения')
    description = models.TextField(blank=True, null=True, verbose_name='Описание')
    
    role_group = models.ForeignKey(
        RoleGroup, 
        on_delete=models.CASCADE, 
        related_name='module_permissions',
        verbose_name='Ролевая группа'
    )
    
    is_granted = models.BooleanField(default=False, verbose_name='Предоставлено')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Разрешение модуля'
        verbose_name_plural = 'Разрешения модулей'
        unique_together = ['module_name', 'permission_key', 'role_group']
        ordering = ['module_name', 'permission_key']
    
    def __str__(self):
        return f"{self.module_name}.{self.permission_key} - {self.role_group.name}"


class RegistrationInvitation(models.Model):
    """Приглашение на регистрацию в системе."""

    email = models.EmailField(verbose_name='Email')
    token = models.CharField(max_length=64, unique=True, db_index=True, verbose_name='Токен')
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_registration_invitations',
        verbose_name='Пригласил',
    )
    note = models.CharField(max_length=255, blank=True, default='', verbose_name='Примечание')
    expires_at = models.DateTimeField(verbose_name='Действует до')
    used_at = models.DateTimeField(null=True, blank=True, verbose_name='Использовано')
    used_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='registration_invitation',
        verbose_name='Зарегистрировался',
    )
    is_revoked = models.BooleanField(default=False, verbose_name='Отозвано')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создано')

    class Meta:
        app_label = 'cms_adp'
        verbose_name = 'Приглашение на регистрацию'
        verbose_name_plural = 'Приглашения на регистрацию'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.email} ({self.token[:8]}...)'


class UserProfileChangeRequest(models.Model):
    """Заявка пользователя на изменение email, ФИО и телефона."""

    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'На рассмотрении'),
        (STATUS_APPROVED, 'Одобрено'),
        (STATUS_REJECTED, 'Отклонено'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile_change_requests',
        verbose_name='Пользователь',
    )
    email = models.EmailField(blank=True, default='', verbose_name='Email')
    first_name = models.CharField(max_length=150, blank=True, default='', verbose_name='Имя')
    last_name = models.CharField(max_length=150, blank=True, default='', verbose_name='Фамилия')
    middle_name = models.CharField(max_length=150, blank=True, default='', verbose_name='Отчество')
    phone = models.CharField(max_length=20, blank=True, default='', verbose_name='Телефон')
    comment = models.CharField(max_length=500, blank=True, default='', verbose_name='Комментарий пользователя')
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name='Статус',
    )
    admin_comment = models.CharField(max_length=500, blank=True, default='', verbose_name='Комментарий администратора')
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_profile_change_requests',
        verbose_name='Обработал',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата обработки')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создано')

    class Meta:
        app_label = 'cms_adp'
        verbose_name = 'Заявка на изменение данных профиля'
        verbose_name_plural = 'Заявки на изменение данных профиля'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
        ]

    def __str__(self):
        return f'{self.user_id}: {self.last_name} {self.first_name} ({self.status})'


# Модели меню живут в подпакете; без импорта Django не видит их в cms_adp
# и makemigrations ошибочно предлагает DeleteModel (см. 0051).
from src.core.cms.adp.menu.models import (  # noqa: E402, F401
    MenuAccessLog,
    MenuItem,
    MenuSeparator,
)