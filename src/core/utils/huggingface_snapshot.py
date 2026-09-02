"""Локальный снимок Hugging Face для sentence-transformers и аналогов."""

from __future__ import annotations

import sys
from pathlib import Path


def is_huggingface_repo_id(repo_id: str) -> bool:
    """True для снимка Hub org/name (не имя библиотеки Ollama и не hf.co/…)."""
    raw = (repo_id or '').strip()
    if not raw:
        return False
    lowered = raw.lower()
    if lowered.startswith('hf.co/') or lowered.startswith('huggingface.co/'):
        return False
    parts = raw.split('/')
    return len(parts) == 2 and all(parts) and ':' not in parts[0]


def _deployment_root(root: Path | None = None) -> Path:
    from src.core.utils.database.module_schema import project_root

    project = (root or project_root()).resolve()
    deploy = str(project / 'core' / 'deployment')
    if deploy not in sys.path:
        sys.path.insert(0, deploy)
    return project


def resolve_huggingface_source(repo_id: str, *, root: Path | None = None) -> str:
    """Путь к готовому снимку или исходный org/name, если весов ещё нет."""
    project = _deployment_root(root)
    from project_layout import resolve_huggingface_source as resolve_on_disk

    return resolve_on_disk(project, repo_id)


def ensure_local_source(repo_id: str, *, root: Path | None = None) -> str:
    """Путь к снимку в trained_models; если весов нет — ставит их туда."""
    project = _deployment_root(root)
    from huggingface.snapshot import ensure_installed

    dest = ensure_installed(project, repo_id)
    return str(dest)
