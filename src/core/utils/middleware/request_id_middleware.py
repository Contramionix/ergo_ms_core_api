"""Проставляет и возвращает X-Request-ID."""

from __future__ import annotations

from src.core.utils.request_id import apply_response_header, request_id_from_meta


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id_from_meta(getattr(request, 'META', {}) or {})
        response = self.get_response(request)
        apply_response_header(response)
        return response
