"""Поиск hook-файлов маршрутов и разрешение путей компонентов."""
from __future__ import annotations

from pathlib import Path

from src.config.paths import MODULES_DIR, SYSTEM_DIR


def iter_module_routes_files(client_dir: Path) -> list[Path]:
    files: list[Path] = []
    main = client_dir / 'js' / 'routes.js'
    if main.is_file():
        files.append(main)
    files.extend(sorted(client_dir.glob('*/js/routes.js')))
    return files


def iter_core_routes_files(core_client_src: Path) -> list[Path]:
    files: list[Path] = []
    cms = core_client_src / 'core' / 'cms' / 'js' / 'routes.js'
    if cms.is_file():
        files.append(cms)
    files.extend(sorted(core_client_src.glob('**/js/routes.js')))
    seen: set[str] = set()
    unique: list[Path] = []
    for path in files:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def module_client_dir(module_name: str) -> Path:
    return MODULES_DIR / module_name / 'client'


def core_client_src(root: Path | None = None) -> Path:
    base = root or SYSTEM_DIR
    return Path(base) / 'core' / 'client' / 'src'


def resolve_component_path(
    spec: str,
    *,
    from_file: Path,
    system_dir: Path | None = None,
    owner: str = '',
) -> Path | None:
    raw = (spec or '').strip().strip('\'"')
    if not raw or raw.startswith('('):
        return None
    root = Path(system_dir or SYSTEM_DIR)
    if raw.startswith('@/modules/'):
        return (root / 'modules' / raw[len('@/modules/'):]).resolve()
    if raw.startswith('@/'):
        return (root / 'core' / 'client' / 'src' / raw[2:]).resolve()
    if raw.startswith('.'):
        return (from_file.parent / raw).resolve()
    if owner and not raw.startswith('/'):
        candidate = MODULES_DIR / owner / 'client' / raw
        if candidate.is_file():
            return candidate.resolve()
    return None


def is_same_owner_vue(path: Path, *, owner: str, system_dir: Path | None = None) -> bool:
    """Vue того же модуля или ядра — чужие modules/<другое> не трогаем."""
    try:
        resolved = path.resolve()
    except OSError:
        return False
    root = Path(system_dir or SYSTEM_DIR).resolve()
    if owner:
        prefix = (MODULES_DIR / owner).resolve()
        try:
            resolved.relative_to(prefix)
            return True
        except ValueError:
            return False
    core_src = (root / 'core' / 'client' / 'src').resolve()
    try:
        resolved.relative_to(core_src)
        return True
    except ValueError:
        return False
