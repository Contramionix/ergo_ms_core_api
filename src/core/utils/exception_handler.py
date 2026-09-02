"""Обработчик исключений DRF: понятный ответ без внутренних деталей."""

from django.utils.translation import gettext as _
from rest_framework.exceptions import Throttled
from rest_framework.views import exception_handler as drf_exception_handler


def too_many_requests_message() -> str:
    return str(_('Слишком много запросов. Попробуйте позже.'))


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None or not isinstance(exc, Throttled):
        return response
    response.data = {'detail': too_many_requests_message()}
    # Клиент рисует оверлей и backoff refresh по Retry-After.
    wait = getattr(exc, 'wait', None)
    if wait is not None:
        try:
            response['Retry-After'] = str(max(int(wait), 1))
        except (TypeError, ValueError):
            pass
    return response
