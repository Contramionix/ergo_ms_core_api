"""Перечитывает decision.json и обновляет лимиты очередей без рестарта воркера."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from src.config.paths import CACHE_DIR
from src.core.utils.celery.concurrency import queue_concurrency_manager

logger = logging.getLogger('celery.concurrency')

_DECISION = CACHE_DIR / 'celery_balance' / 'decision.json'
_watcher_started = False
_watcher_lock = threading.Lock()
_overlay_paused: set[str] = set()


def _load_decision(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def apply_decision_limits(data: dict[str, Any] | None) -> None:
    """Накладывает queue_limits, pause_queues и non_light_cap из overlay."""
    global _overlay_paused
    if not data:
        return
    raw_limits = data.get('queue_limits')
    limits: dict[str, int] = {}
    if isinstance(raw_limits, dict):
        for name, value in raw_limits.items():
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            limits[str(name)] = parsed
    raw_pause = data.get('pause_queues')
    paused = {
        str(item)
        for item in (raw_pause if isinstance(raw_pause, list) else [])
        if str(item).strip()
    }

    for queue_name in list(_overlay_paused - paused):
        queue_concurrency_manager.set_paused(queue_name, False)
    for queue_name in paused:
        queue_concurrency_manager.set_paused(queue_name, True)
    _overlay_paused = set(paused)

    for queue_name, overlay_limit in limits.items():
        if queue_name in paused:
            continue
        if overlay_limit <= 0:
            continue
        current = queue_concurrency_manager.get_queue_limit(queue_name)
        effective = overlay_limit if current <= 0 else min(current, overlay_limit)
        queue_concurrency_manager.update_queue_limit(queue_name, effective)

    raw_classes = data.get('queue_classes')
    classes: dict[str, str] = {}
    if isinstance(raw_classes, dict):
        classes = {
            str(name): str(task_class)
            for name, task_class in raw_classes.items()
            if str(name).strip() and str(task_class).strip()
        }
    else:
        plans = data.get('plans')
        if isinstance(plans, dict):
            for raw_plan in plans.values():
                if not isinstance(raw_plan, dict):
                    continue
                plan_classes = raw_plan.get('queue_classes')
                if isinstance(plan_classes, dict):
                    classes.update(
                        {
                            str(name): str(task_class)
                            for name, task_class in plan_classes.items()
                            if str(name).strip() and str(task_class).strip()
                        }
                    )
    queue_concurrency_manager.set_queue_classes(classes)

    non_light_cap = 0
    raw_cap = data.get('non_light_cap')
    try:
        non_light_cap = int(raw_cap) if raw_cap is not None else 0
    except (TypeError, ValueError):
        non_light_cap = 0
    if non_light_cap <= 0:
        plans = data.get('plans')
        if isinstance(plans, dict):
            caps = []
            for raw_plan in plans.values():
                if not isinstance(raw_plan, dict):
                    continue
                try:
                    parsed = int(raw_plan.get('non_light_cap') or 0)
                except (TypeError, ValueError):
                    parsed = 0
                if parsed > 0:
                    caps.append(parsed)
            if caps:
                non_light_cap = min(caps)
    queue_concurrency_manager.set_non_light_cap(max(0, non_light_cap))


def reload_overlay_limits(path: Path | None = None) -> bool:
    data = _load_decision(path or _DECISION)
    if data is None:
        return False
    apply_decision_limits(data)
    return True


def start_overlay_limit_watcher(*, interval_sec: float = 30.0) -> None:
    global _watcher_started
    with _watcher_lock:
        if _watcher_started:
            return
        _watcher_started = True

    reload_overlay_limits()

    def _loop() -> None:
        while True:
            try:
                reload_overlay_limits()
            except Exception:
                logger.debug('Не удалось обновить лимиты очередей из overlay', exc_info=True)
            threading.Event().wait(max(5.0, float(interval_sec)))

    thread = threading.Thread(
        target=_loop,
        name='celery-balance-overlay-limits',
        daemon=True,
    )
    thread.start()
