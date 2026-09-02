# -*- coding: utf-8 -*-
"""createsuperuser с обязательной политикой пароля из .env (API_PASSWORD_*)."""

from __future__ import annotations

import builtins
from contextlib import contextmanager

from django.contrib.auth.management.commands.createsuperuser import (
    Command as DjangoCreateSuperuserCommand,
)
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import CommandError

from src.core.cms.adp.password_policy import get_password_requirement_hints

# Подстрока штатного вопроса Django (не переводится gettext).
_BYPASS_PROMPT_NEEDLE = 'Bypass password validation'


def _invalid_password_error(exc: ValidationError) -> CommandError:
    messages = list(exc.messages)
    body = '\n'.join(messages) if messages else str(exc)
    return CommandError(
        'Пароль не соответствует политике из .env (API_PASSWORD_*):\n' + body
    )


def _validate_password_or_raise(password, user=None) -> None:
    try:
        validate_password(password, user)
    except ValidationError as exc:
        raise _invalid_password_error(exc) from exc


class Command(DjangoCreateSuperuserCommand):
    help = (
        'Создаёт суперпользователя. Пароль обязан соответствовать '
        'API_PASSWORD_* из .env; обойти проверку нельзя.'
    )

    def handle(self, *args, **options):
        self._write_policy_hints()
        with self._enforce_env_password_policy():
            return super().handle(*args, **options)

    def _write_policy_hints(self) -> None:
        hints = get_password_requirement_hints()
        if not hints:
            return
        self.stdout.write(
            'Политика пароля (API_PASSWORD_* из .env): ' + '; '.join(hints)
        )

    @contextmanager
    def _enforce_env_password_policy(self):
        # Django в интерактиве предлагает обойти AUTH_PASSWORD_VALIDATORS,
        # а в --noinput не проверяет пароль. Политика из .env обязательна.
        original_input = builtins.input
        manager_cls = type(self.UserModel._default_manager)
        original_create = manager_cls.create_superuser
        user_model = self.UserModel

        def refuse_bypass(prompt=''):
            if _BYPASS_PROMPT_NEEDLE in str(prompt):
                self.stderr.write(
                    'Пароль не соответствует политике из .env. '
                    'Создание с таким паролем запрещено — введите другой.'
                )
                return 'n'
            return original_input(prompt)

        def create_superuser(
            manager,
            username,
            email=None,
            password=None,
            **extra_fields,
        ):
            if password:
                fake_kwargs = {user_model.USERNAME_FIELD: username}
                if email:
                    fake_kwargs['email'] = email
                try:
                    fake_user = user_model(**fake_kwargs)
                except Exception:
                    fake_user = None
                _validate_password_or_raise(password, fake_user)
            return original_create(
                manager,
                username,
                email=email,
                password=password,
                **extra_fields,
            )

        builtins.input = refuse_bypass
        manager_cls.create_superuser = create_superuser
        try:
            yield
        finally:
            builtins.input = original_input
            manager_cls.create_superuser = original_create
