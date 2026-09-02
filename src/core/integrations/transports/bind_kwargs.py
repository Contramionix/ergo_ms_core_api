"""Отсекает kwargs, которые обработчик моста не принимает."""

from __future__ import annotations

import inspect
from typing import Any, Callable


def kwargs_accepted_by_handler(handler: Callable[..., Any], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Оставляет только имена из сигнатуры. ``**kwargs`` у обработчика — пропускает всё."""
    target = inspect.unwrap(handler)
    try:
        sig = inspect.signature(target)
    except (TypeError, ValueError):
        return kwargs
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in sig.parameters.values()):
        return kwargs
    names = {
        name
        for name, param in sig.parameters.items()
        if param.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }
    return {key: value for key, value in kwargs.items() if key in names}
