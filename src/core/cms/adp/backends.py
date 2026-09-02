"""Аутентификация по username или email."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


def get_user_by_login(login: str):
    """
    Находит пользователя по логину или email.

    Сначала точное совпадение username, затем ровно один email__iexact
    (только если в строке есть «@»). При 0 или >1 совпадений по email — None.
    """
    UserModel = get_user_model()
    identifier = (login or '').strip()
    if not identifier:
        return None

    try:
        return UserModel.objects.get(**{UserModel.USERNAME_FIELD: identifier})
    except UserModel.DoesNotExist:
        pass
    except UserModel.MultipleObjectsReturned:
        return None

    if '@' not in identifier:
        return None

    email = identifier.lower()
    qs = UserModel.objects.filter(email__iexact=email).exclude(
        Q(email='') | Q(email__isnull=True),
    )
    try:
        return qs.get()
    except (UserModel.DoesNotExist, UserModel.MultipleObjectsReturned):
        return None


class EmailOrUsernameModelBackend(ModelBackend):
    """ModelBackend с входом по username или email."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if username is None or password is None:
            return None

        user = get_user_by_login(username)
        if user is None:
            # Как у Django ModelBackend — сглаживаем timing при отсутствии пользователя.
            UserModel().set_password(password)
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
