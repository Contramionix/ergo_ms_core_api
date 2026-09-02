"""
Автообнаружение каталогов прав модулей.

Сканирует все обнаруженные приложения и собирает PERMISSION_CATALOG
из файлов permission_catalog.py каждого модуля. Результат кешируется
в памяти процесса.
"""
import importlib
import logging
from typing import Any, Dict, FrozenSet, List, Optional

logger = logging.getLogger('utils')

_catalog_cache: Optional[Dict] = None


def _load_catalogs() -> Dict:
    from src.core.utils.auto_api.discovered_apps_cache import get_discovered_apps

    catalogs = {}
    all_keys = {}
    key_to_module = {}

    for app_path in get_discovered_apps():
        catalog_module_path = f'{app_path}.permission_catalog'
        try:
            mod = importlib.import_module(catalog_module_path)
        except ImportError:
            continue
        except Exception:
            logger.warning(
                'Ошибка при загрузке permission_catalog из %s',
                catalog_module_path,
                exc_info=True,
            )
            continue

        catalog = getattr(mod, 'PERMISSION_CATALOG', None)
        if not catalog or not isinstance(catalog, dict):
            continue

        module_name = catalog.get('module_name')
        permissions = catalog.get('permissions')
        if not module_name or not isinstance(permissions, dict):
            continue

        catalogs[module_name] = catalog
        all_keys.update(permissions)

        for perm_key in permissions:
            key_to_module[perm_key] = module_name

    return {
        'catalogs': catalogs,
        'all_keys': all_keys,
        'key_to_module': key_to_module,
    }


def _get_cache() -> Dict:
    global _catalog_cache
    if _catalog_cache is None:
        _catalog_cache = _load_catalogs()
    return _catalog_cache


def get_all_permission_keys() -> Dict[str, str]:
    """Единый словарь {permission_key: human_readable_name} по всем модулям."""
    return dict(_get_cache()['all_keys'])


def get_view_permission_pairs() -> set[tuple[str, str]]:
    """Пары (module_name, permission_key) для прав просмотра из каталогов."""
    pairs: set[tuple[str, str]] = set()
    for module_name, catalog in _get_cache()['catalogs'].items():
        for key in catalog.get('permissions') or {}:
            if module_name and str(key).endswith('_view'):
                pairs.add((module_name, str(key)))
    return pairs


def get_module_names() -> List[str]:
    """Список имён модулей, у которых есть каталог прав."""
    return list(_get_cache()['catalogs'].keys())


def resolve_module_name(permission_key: str) -> Optional[str]:
    """Определяет имя модуля по ключу права без хардкода."""
    return _get_cache()['key_to_module'].get(permission_key)


def get_module_permission_keys(module_name: str) -> Dict[str, str]:
    """Словарь прав конкретного модуля."""
    catalog = _get_cache()['catalogs'].get(module_name)
    if not catalog:
        return {}
    return dict(catalog.get('permissions', {}))


def _format_module_slug(module_name: str) -> str:
    """Читаемая подпись из slug папки модуля."""
    return module_name.replace('-', ' ').replace('_', ' ').strip().title()


def _is_slug_like_module_label(module_name: str, label: str) -> bool:
    """True если label — лишь title-case slug (Bi_Analysis / Bi Analysis), не заданное имя."""
    normalized = (label or '').strip()
    if not normalized or not module_name:
        return True
    key = module_name.strip()
    if normalized == key:
        return True
    if normalized == key.title():
        return True
    if normalized.lower() == _format_module_slug(key).lower():
        return True
    return False


_help_title_cache: Optional[Dict[str, str]] = None


def _help_yaml_module_title(module_name: str) -> Optional[str]:
    """Русский title из modules/<name>/ergoms.help.yaml (source locale)."""
    global _help_title_cache
    if _help_title_cache is None:
        _help_title_cache = {}
        try:
            import yaml
            from src.config.settings.base import MODULES_DIR
        except Exception:
            return None
        if not MODULES_DIR.is_dir():
            return None
        for path in MODULES_DIR.glob('*/ergoms.help.yaml'):
            try:
                data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
            except Exception:
                continue
            title = data.get('title')
            if not isinstance(title, str) or not title.strip():
                continue
            title = title.strip()
            _help_title_cache[path.parent.name] = title
            mod = data.get('module')
            if isinstance(mod, str) and mod.strip():
                _help_title_cache[mod.strip()] = title
    return _help_title_cache.get(module_name)


def canonicalize_module_name(module_name: str) -> str:
    """Свести вложенный label/сегмент к имени установленного модуля (child→parent по префиксу)."""
    if not module_name:
        return 'core'
    key = module_name.strip()
    if not key or key == 'core':
        return 'core'

    from src.config.settings.base import MODULES_DIR
    from src.core.utils.module_registry import get_installed_module_names

    installed = get_installed_module_names(include_disabled=True)
    installed_set = set(installed)
    if key in installed_set:
        return key

    for name in sorted(installed, key=len, reverse=True):
        if key.startswith(f'{name}_') or key.startswith(f'{name}-'):
            return name

    try:
        from django.apps import apps

        try:
            cfg = apps.get_app_config(key)
            parts = (getattr(cfg, 'name', '') or '').split('.')
            if len(parts) >= 2 and parts[0] == 'modules' and parts[1] in installed_set:
                return parts[1]
        except LookupError:
            pass

        for cfg in apps.get_app_configs():
            parts = (getattr(cfg, 'name', '') or '').split('.')
            if (
                len(parts) >= 2
                and parts[0] == 'modules'
                and parts[1] in installed_set
                and (cfg.label == key or parts[-1] == key)
            ):
                return parts[1]
    except Exception:
        pass

    if MODULES_DIR.is_dir():
        for name in installed:
            if (MODULES_DIR / name / 'api' / key).is_dir():
                return name

    return key


def _app_config_verbose_name(module_name: str) -> Optional[str]:
    """verbose_name AppConfig модуля (label или modules.<name>.api)."""
    if not module_name or module_name == 'core':
        return None

    from django.apps import apps

    candidates = []
    try:
        candidates.append(apps.get_app_config(module_name))
    except LookupError:
        pass

    target = f'modules.{module_name}.api'
    for app_config in apps.get_app_configs():
        name = getattr(app_config, 'name', '') or ''
        if name == target or name.startswith(f'{target}.'):
            candidates.append(app_config)

    for app_config in candidates:
        verbose_name = getattr(app_config, 'verbose_name', None)
        if isinstance(verbose_name, str) and verbose_name.strip():
            # Пропускаем дефолт Django AppConfig: label.title() → Bi_Analysis
            if not _is_slug_like_module_label(module_name, verbose_name):
                return verbose_name.strip()
    return None


def _resolve_module_label(module_name: str, catalog: Optional[Dict] = None) -> str:
    """Человекочитаемое имя модуля: каталог → help.yaml → AppConfig → slug."""
    key = canonicalize_module_name(module_name)
    if catalog is None:
        catalogs = _get_cache().get('catalogs') or {}
        catalog = catalogs.get(key)

    if catalog:
        label = catalog.get('module_label')
        if isinstance(label, str) and label.strip() and not _is_slug_like_module_label(key, label):
            return label.strip()

    help_title = _help_yaml_module_title(key)
    if help_title and not _is_slug_like_module_label(key, help_title):
        return help_title

    resolved = _app_config_verbose_name(key)
    if resolved:
        return resolved

    return _format_module_slug(key)


def _merge_stored_module_permissions(modules: List[Dict[str, Any]], *, disabled: FrozenSet[str]) -> None:
    """Дополняет каталог ключами прав, уже сохранёнными в ModulePermission."""
    from src.core.cms.adp.models import ModulePermission

    by_name = {item['module_name']: item for item in modules}
    rows = (
        ModulePermission.objects
        .values('module_name', 'permission_key', 'permission_name')
        .order_by('module_name', 'permission_key')
        .distinct()
    )
    for row in rows:
        module_name = canonicalize_module_name(row['module_name'] or '')
        if module_name not in by_name:
            by_name[module_name] = {
                'module_name': module_name,
                'module_label': _resolve_module_label(module_name),
                'user_description': '',
                'has_permission_catalog': False,
                'permissions': {},
                'disabled': module_name in disabled,
            }
            modules.append(by_name[module_name])

        permissions = by_name[module_name]['permissions']
        key = row['permission_key']
        if key and key not in permissions:
            label = (row.get('permission_name') or '').strip()
            permissions[key] = label or key

    modules.sort(key=lambda item: item['module_name'])


def get_modules_catalog(*, include_disabled: bool = False) -> List[Dict[str, Any]]:
    """
    Централизованный каталог модулей для UI и ADP.

    Список — только установленные модули из ``modules/`` (есть ``api/``
    и/или ``client/``; пустые placeholder-папки не включаются). Права
    подсказываются из permission_catalog.py и из уже сохранённых
    записей ModulePermission в БД.
    """
    from src.core.utils.module_registry import get_disabled_modules, get_installed_module_names

    disabled = get_disabled_modules()
    permission_catalogs = _get_cache()['catalogs']
    modules: List[Dict[str, Any]] = []

    for module_name in get_installed_module_names(include_disabled=include_disabled):
        catalog = permission_catalogs.get(module_name)
        permissions = dict(catalog.get('permissions', {})) if catalog else {}
        raw_description = catalog.get('user_description') if catalog else None
        user_description = (
            raw_description.strip()
            if isinstance(raw_description, str) and raw_description.strip()
            else ''
        )
        modules.append({
            'module_name': module_name,
            'module_label': _resolve_module_label(module_name, catalog),
            'user_description': user_description,
            'has_permission_catalog': bool(catalog and catalog.get('permissions')),
            'permissions': permissions,
            'disabled': module_name in disabled,
        })

    _merge_stored_module_permissions(modules, disabled=disabled)
    return modules


def clear_cache() -> None:
    """Сбрасывает кэш каталога прав и подписей модулей."""
    global _catalog_cache, _help_title_cache
    _catalog_cache = None
    _help_title_cache = None
