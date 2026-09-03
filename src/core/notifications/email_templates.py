"""
Рендеринг формы письма для уведомления.

Каскад выбора (от частного к общему):
1. bridge.call('notifications.render_email.<module>', notification=...) — programmatic override,
   модуль возвращает dict {'subject', 'html_body', 'text_body', 'from_email'}.
2. Спека события из каталога (channels.email: subject / template_html / template_text).
3. Module provider 'notifications.email_templates'[key=module].get_spec(event_key)
   и его MODULE_DEFAULT.
4. Core fallback: notifications/email/default.html + title/body уведомления.

Контекст: базовый формирует ядро (build_base_email_context), модуль может
обогатить его через provide_many('notifications.email_context', key=<module>, obj=<callable>).
"""

import logging
from dataclasses import dataclass

from django.conf import settings
from django.template import Context, Template
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from src.core.integrations import bridge
from src.core.integrations.exceptions import BridgeUnavailable
from src.core.integrations.module_contracts import (
    NOTIFICATIONS_EMAIL_CONTEXT_GROUP,
    NOTIFICATIONS_EMAIL_TEMPLATES_GROUP,
    NOTIFICATIONS_RENDER_EMAIL_PREFIX,
)

from . import catalog
from .frontend_route_url import (
    build_frontend_url_from_route,
    inbox_url,
    join_frontend_path,
)

logger = logging.getLogger('core.notifications')

EMAIL_CONTEXT_GROUP = NOTIFICATIONS_EMAIL_CONTEXT_GROUP
EMAIL_TEMPLATES_GROUP = NOTIFICATIONS_EMAIL_TEMPLATES_GROUP

CORE_DEFAULT_HTML = 'notifications/email/default.html'
CORE_DEFAULT_TXT = 'notifications/email/default.txt'


@dataclass
class RenderedEmail:
    subject: str
    html_body: str
    text_body: str
    from_email: str | None = None


def _resolve_action_url(notification) -> str:
    """Ссылка в письме: link_url → Vue-route → инбокс (без числового pk)."""
    base = getattr(settings, 'FRONTEND_BASE_URL', '') or ''
    link = (getattr(notification, 'link_url', None) or '').strip()
    if link:
        if link.lower().startswith(('http://', 'https://')):
            return link
        return join_frontend_path(base, link)
    routed = build_frontend_url_from_route(getattr(notification, 'route', None), base_url=base)
    if routed:
        return routed
    return inbox_url(base) if base else ''


def resolve_unsubscribe_url() -> str:
    base = getattr(settings, 'FRONTEND_BASE_URL', '') or ''
    return inbox_url(base) if base else ''


def build_base_email_context(notification) -> dict:
    recipient = notification.recipient
    full_name = ''
    if recipient is not None:
        full_name = (
            f"{getattr(recipient, 'last_name', '')} {getattr(recipient, 'first_name', '')}"
        ).strip() or getattr(recipient, 'username', '')

    section = catalog.get_catalog().get(notification.source_module or '')
    module_label = section['module_label'] if section else (notification.source_module or '')

    return {
        'recipient': recipient,
        'recipient_name': full_name,
        'title': notification.title,
        'body': notification.body,
        'level': notification.level,
        'source_module': notification.source_module,
        'event_key': notification.event_key,
        'module_label': module_label,
        'action_url': _resolve_action_url(notification),
        'unsubscribe_url': resolve_unsubscribe_url(),
        'meta': notification.meta or {},
    }


def _enrich_context(notification, base_context: dict) -> dict:
    provider = bridge.all(EMAIL_CONTEXT_GROUP).get(notification.source_module or '')
    if not callable(provider):
        return base_context
    try:
        enriched = provider(notification=notification, base_context=base_context)
        if isinstance(enriched, dict):
            return enriched
    except Exception:
        logger.exception(
            'email_context провайдер %s упал для notification=%s',
            notification.source_module, notification.pk,
        )
    return base_context


def _render_subject(template_string: str, context: dict, fallback: str) -> str:
    if not template_string:
        return fallback
    try:
        rendered = Template(template_string).render(Context(context)).strip()
        return rendered or fallback
    except Exception:
        logger.exception('Ошибка рендера subject-шаблона: %r', template_string)
        return fallback


def _render_template(template_name: str, context: dict) -> str | None:
    if not template_name:
        return None
    try:
        return render_to_string(template_name, context)
    except Exception:
        logger.exception('Шаблон письма %r не отрендерился', template_name)
        return None


def _try_programmatic(notification, context: dict) -> RenderedEmail | None:
    op = f'{NOTIFICATIONS_RENDER_EMAIL_PREFIX}{notification.source_module or ""}'
    if not notification.source_module:
        return None
    try:
        if not bridge.has(op):
            return None
    except BridgeUnavailable:
        logger.warning('Programmatic render %s: модуль не ответил', op)
        return None
    try:
        result = bridge.call(op, notification=notification, base_context=context)
    except Exception:
        logger.exception('Programmatic render %s упал', op)
        return None
    if not isinstance(result, dict) or not result.get('html_body'):
        return None
    return RenderedEmail(
        subject=result.get('subject') or notification.title,
        html_body=result['html_body'],
        text_body=result.get('text_body') or strip_tags(result['html_body']),
        from_email=result.get('from_email'),
    )


def _get_module_spec(notification) -> dict | None:
    """Спека из module provider (уровень D каскада)."""
    provider = bridge.all(EMAIL_TEMPLATES_GROUP).get(notification.source_module or '')
    if provider is None:
        return None
    spec = None
    get_spec = getattr(provider, 'get_spec', None)
    if callable(get_spec):
        try:
            spec = get_spec(notification.event_key)
        except Exception:
            logger.exception(
                'email_templates провайдер %s упал', notification.source_module,
            )
    if spec is None:
        module_default = getattr(provider, 'MODULE_DEFAULT', '')
        if module_default:
            spec = {'template_html': module_default}
    return spec if isinstance(spec, dict) else None


def _render_from_spec(spec: dict, notification, context: dict) -> RenderedEmail | None:
    html = _render_template(spec.get('template_html') or '', context)
    if html is None and not spec.get('subject'):
        return None
    if html is None:
        html = render_to_string(CORE_DEFAULT_HTML, context)
    text = _render_template(spec.get('template_text') or '', context) or strip_tags(html)
    subject = _render_subject(spec.get('subject') or '', context, notification.title)
    return RenderedEmail(subject=subject, html_body=html, text_body=text)


class EmailTemplateResolver:
    """Точка входа: resolve(notification) -> RenderedEmail."""

    @staticmethod
    def resolve(notification) -> RenderedEmail:
        context = _enrich_context(notification, build_base_email_context(notification))

        rendered = _try_programmatic(notification, context)
        if rendered is not None:
            return rendered

        event_spec = catalog.get_event_spec(notification.source_module, notification.event_key)
        if event_spec is not None:
            email_spec = event_spec['channels'].get(catalog.CHANNEL_EMAIL) or {}
            if email_spec.get('template_html') or email_spec.get('subject'):
                rendered = _render_from_spec(email_spec, notification, context)
                if rendered is not None:
                    return rendered

        module_spec = _get_module_spec(notification)
        if module_spec is not None:
            rendered = _render_from_spec(module_spec, notification, context)
            if rendered is not None:
                return rendered

        html = render_to_string(CORE_DEFAULT_HTML, context)
        text = _render_template(CORE_DEFAULT_TXT, context) or strip_tags(html)
        return RenderedEmail(subject=notification.title, html_body=html, text_body=text)
