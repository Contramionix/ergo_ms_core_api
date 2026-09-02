"""Фактический footprint задач Celery (RSS/wall-time, без payload)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from src.config.paths import CACHE_DIR

_HISTORY_DIR = CACHE_DIR / 'celery_balance'
_HISTORY_FILE = _HISTORY_DIR / 'history.jsonl'
_local = threading.local()


def _vram_mb_for_pid(pid: int | None = None) -> float:
    binary = shutil.which('nvidia-smi')
    if not binary:
        return 0.0
    target = int(pid or os.getpid())
    try:
        result = subprocess.run(
            [
                binary,
                '--query-compute-apps=pid,used_gpu_memory',
                '--format=csv,noheader,nounits',
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    if result.returncode != 0 or not result.stdout.strip():
        return 0.0
    total = 0.0
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(',')]
        if len(parts) < 2:
            continue
        try:
            if int(float(parts[0])) != target:
                continue
            total += float(parts[1])
        except ValueError:
            continue
    return round(total, 1)


def _rss_mb() -> float:
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        return 0.0


def _queue_name(sender, task_id: str, kwargs: dict) -> str:
    request = getattr(sender, 'request', None)
    if request is not None:
        delivery = getattr(request, 'delivery_info', None) or {}
        for key in ('routing_key', 'exchange'):
            value = delivery.get(key)
            if value:
                return str(value)
        queue = getattr(request, 'queue', None)
        if queue:
            return str(queue)
    headers = kwargs.get('headers') if isinstance(kwargs, dict) else None
    if isinstance(headers, dict) and headers.get('queue'):
        return str(headers['queue'])
    return 'default'


def on_task_prerun(sender=None, task_id=None, task=None, **kwargs) -> None:
    _local.start_mono = time.monotonic()
    _local.start_rss_mb = _rss_mb()
    _local.peak_rss_mb = _local.start_rss_mb
    _local.peak_vram_mb = _vram_mb_for_pid()


def on_task_postrun(sender=None, task_id=None, task=None, **kwargs) -> None:
    start = getattr(_local, 'start_mono', None)
    if start is None:
        return
    wall_ms = (time.monotonic() - start) * 1000.0
    peak = max(float(getattr(_local, 'peak_rss_mb', 0.0)), _rss_mb())
    peak_vram = max(float(getattr(_local, 'peak_vram_mb', 0.0)), _vram_mb_for_pid())
    task_name = getattr(sender, 'name', None) or getattr(task, 'name', None) or 'unknown'
    queue = _queue_name(sender or task, task_id or '', kwargs)
    _append(
        queue=queue,
        task_name=str(task_name),
        wall_ms=wall_ms,
        peak_rss_mb=peak,
        peak_vram_mb=peak_vram,
    )
    _local.start_mono = None


def _append(
    *,
    queue: str,
    task_name: str,
    wall_ms: float,
    peak_rss_mb: float,
    peak_vram_mb: float = 0.0,
) -> None:
    try:
        _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'queue': queue,
            'task_name': task_name,
            'wall_ms': round(float(wall_ms), 1),
            'peak_rss_mb': round(float(peak_rss_mb), 1),
            'peak_vram_mb': round(float(peak_vram_mb), 1),
        }
        with _HISTORY_FILE.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    except OSError:
        return


def connect_signals(celery_app) -> None:
    from celery.signals import task_postrun, task_prerun

    task_prerun.connect(on_task_prerun, weak=False)
    task_postrun.connect(on_task_postrun, weak=False)
