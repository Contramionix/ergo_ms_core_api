"""Домен для SMTP HELO/EHLO: не FQDN VPS (*.twc1.net), иначе фильтры (DBL_SPAM)."""

from __future__ import annotations

from email.utils import parseaddr
from urllib.parse import urlparse

from django.conf import settings


def _domain_from_address(value: str | None) -> str:
    if not value or not isinstance(value, str):
        return ''
    _name, addr = parseaddr(value.strip())
    candidate = addr or value.strip()
    if '@' not in candidate:
        return ''
    return candidate.rsplit('@', 1)[-1].strip().lower()


def resolve_smtp_local_hostname(*, from_email: str | None = None) -> str:
    """
    Hostname для SMTP local_hostname (HELO/EHLO).

    Порядок: EMAIL_LOCAL_HOSTNAME → домен From → EMAIL_HOST_USER / DEFAULT_FROM_EMAIL
    → FRONTEND_BASE_URL → localhost (не socket.getfqdn() VPS).
    """
    configured = (getattr(settings, 'EMAIL_LOCAL_HOSTNAME', None) or '').strip()
    if configured:
        return configured.lower().rstrip('.')

    for candidate in (
        from_email,
        getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        getattr(settings, 'EMAIL_HOST_USER', None),
    ):
        domain = _domain_from_address(candidate if isinstance(candidate, str) else None)
        if domain:
            return domain

    base = getattr(settings, 'FRONTEND_BASE_URL', '') or ''
    host = urlparse(base).hostname
    if host:
        return host.lower().rstrip('.')

    return 'localhost'


def apply_django_smtp_helo_override() -> None:
    """Подменяет кэш DNS_NAME, который Django SMTP EmailBackend передаёт в HELO."""
    hostname = resolve_smtp_local_hostname()
    if not hostname or hostname == 'localhost':
        return
    from django.core.mail.utils import DNS_NAME

    # Django 5.2+: атрибут кэша — _fqdn (не _cached_get_fqdn)
    DNS_NAME._fqdn = hostname
