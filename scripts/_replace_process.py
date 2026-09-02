"""Замена текущего процесса на команду сервера (без висящего родителя)."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence


def replace_current_process(
    cmd: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    """Unix: execvpe. Windows: wait — overlay там завершает родителя и оставляет сироту."""
    argv = [str(part) for part in cmd]
    if not argv:
        return 2
    run_env = dict(env) if env is not None else os.environ.copy()
    if cwd:
        os.chdir(str(cwd))
    # execvpe на Windows — _P_OVERLAY: новый процесс стартует, текущий сразу
    # выходит. Тогда ergoms / задача VS Code считают сервис уже остановленным,
    # а Daphne остаётся сиротой и занимает порт при следующем запуске.
    if os.name == 'nt':
        return subprocess.call(argv, env=run_env)
    try:
        os.execvpe(argv[0], argv, run_env)
    except OSError:
        return subprocess.call(argv, env=run_env)
    return 1
