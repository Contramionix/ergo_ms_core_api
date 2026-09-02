import json
import re
from dataclasses import dataclass

from src.config.settings.base import SYSTEM_DIR
from src.core.utils.auto_api.auto_config import ModuleDiscoverer

_CATCH_ALL_PATH = '/:pathMatch(.*)*'

# "path": "..." / path: '...' — с кавычками у ключа и без
_STRING_PROP_RE = re.compile(
    r'''(?:["'](?P<key>path|title|titleKey|name)["']|(?P<key_bare>path|title|titleKey|name))\s*:\s*["'](?P<value>[^"']*)["']'''
)


def normalize_cms_path(path: str) -> str:
    if not path:
        return path
    if path == '/':
        return '/'
    return path[:-1] if path.endswith('/') else path


def _is_cms_route_path(path: str) -> bool:
    """Только абсолютные пути для CMSPage; без catch-all и относительных сегментов."""
    if not path or path == _CATCH_ALL_PATH or 'pathMatch' in path:
        return False
    return path.startswith('/')


def _join_route_paths(parent: str, child: str) -> str:
    """Склеивает parent + relative child в абсолютный путь Vue Router."""
    child = (child or '').strip()
    if not child:
        return parent
    if child.startswith('/'):
        return child
    if not parent:
        return f'/{child}'
    if parent.endswith('/'):
        return f'{parent}{child}'
    return f'{parent}/{child}'


def _find_matching_brace(content: str, open_idx: int) -> int:
    """Индекс символа после закрывающей `}` для `{` на open_idx, либо -1."""
    if open_idx < 0 or open_idx >= len(content) or content[open_idx] != '{':
        return -1

    depth = 0
    in_string = None
    escape = False
    for index in range(open_idx, len(content)):
        char = content[index]
        if in_string:
            if escape:
                escape = False
            elif char == '\\':
                escape = True
            elif char == in_string:
                in_string = None
            continue

        if char in ('"', "'"):
            in_string = char
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return index + 1
    return -1


def _find_matching_bracket(content: str, open_idx: int) -> int:
    """Индекс символа после закрывающей `]` для `[` на open_idx, либо -1."""
    if open_idx < 0 or open_idx >= len(content) or content[open_idx] != '[':
        return -1

    depth = 0
    in_string = None
    escape = False
    for index in range(open_idx, len(content)):
        char = content[index]
        if in_string:
            if escape:
                escape = False
            elif char == '\\':
                escape = True
            elif char == in_string:
                in_string = None
            continue

        if char in ('"', "'"):
            in_string = char
        elif char == '[':
            depth += 1
        elif char == ']':
            depth -= 1
            if depth == 0:
                return index + 1
        elif char == '{' and depth == 1:
            # объекты внутри массива обрабатываются отдельно
            pass
    return -1


def _extract_string_props(block: str) -> dict[str, str]:
    """Достаёт path/title/titleKey из блока (не из вложенного children)."""
    children_match = re.search(
        r'''(?:["']children["']|children)\s*:''',
        block,
    )
    scan_region = block[: children_match.start()] if children_match else block

    props: dict[str, str] = {}
    for match in _STRING_PROP_RE.finditer(scan_region):
        key = match.group('key') or match.group('key_bare')
        value = (match.group('value') or '').replace('\\\\', '\\').strip()
        if key and key not in props:
            props[key] = value

    return props


def _iter_top_level_object_values(content: str, start: int, end: int):
    """Итерирует значения-объекты `{...}` на верхнем уровне объекта [start:end]."""
    index = start
    in_string = None
    escape = False

    while index < end:
        char = content[index]
        if in_string:
            if escape:
                escape = False
            elif char == '\\':
                escape = True
            elif char == in_string:
                in_string = None
            index += 1
            continue

        if char in ('"', "'"):
            in_string = char
            index += 1
            continue

        if char == '{':
            value_end = _find_matching_brace(content, index)
            if value_end < 0:
                return
            yield index, value_end
            index = value_end
            continue

        index += 1


def _iter_array_objects(content: str, start: int, end: int):
    """Итерирует объекты внутри массива `[...]`."""
    index = start
    in_string = None
    escape = False

    while index < end:
        char = content[index]
        if in_string:
            if escape:
                escape = False
            elif char == '\\':
                escape = True
            elif char == in_string:
                in_string = None
            index += 1
            continue

        if char in ('"', "'"):
            in_string = char
            index += 1
            continue

        if char == '{':
            value_end = _find_matching_brace(content, index)
            if value_end < 0:
                return
            yield index, value_end
            index = value_end
            continue

        index += 1


def _find_children_region(block: str) -> tuple[str, int, int] | None:
    """
    Возвращает ('object'|'array', absolute_start, absolute_end) региона children
    внутри block, либо None.
    """
    match = re.search(r'''(?:["']children["']|children)\s*:\s*([\[{])''', block)
    if not match:
        return None

    open_char = match.group(1)
    open_idx = match.end(1) - 1
    if open_char == '{':
        end = _find_matching_brace(block, open_idx)
        if end < 0:
            return None
        return 'object', open_idx + 1, end - 1
    end = _find_matching_bracket(block, open_idx)
    if end < 0:
        return None
    return 'array', open_idx + 1, end - 1


def _route_key_before_brace(content: str, brace_idx: int) -> str:
    """Имя свойства перед `{` (`ImpulsAnalysis:` / `"Foo":`)."""
    index = brace_idx - 1
    while index >= 0 and content[index] in ' \t\n\r':
        index -= 1
    if index < 0 or content[index] != ':':
        return ''
    index -= 1
    while index >= 0 and content[index] in ' \t\n\r':
        index -= 1
    if index < 0:
        return ''
    if content[index] in ('"', "'"):
        quote = content[index]
        end = index
        index -= 1
        while index >= 0 and content[index] != quote:
            index -= 1
        if index < 0:
            return ''
        return content[index + 1 : end]
    end = index
    while index >= 0 and (content[index].isalnum() or content[index] in '_$'):
        index -= 1
    return content[index + 1 : end + 1]


def _merge_catalog_entry(
    catalog: dict[str, dict[str, str]],
    path: str,
    module_name: str,
    title: str = '',
    title_key: str = '',
    route_name: str = '',
) -> None:
    normalized = normalize_cms_path(path)
    if not _is_cms_route_path(normalized):
        return

    if title == '-':
        title = ''

    existing = catalog.get(normalized, {})
    if title and not existing.get('title'):
        existing['title'] = title
    if title_key and not existing.get('title_key'):
        existing['title_key'] = title_key
    if route_name:
        names = existing.setdefault('route_names', [])
        if isinstance(names, str):
            names = [names] if names else []
            existing['route_names'] = names
        if route_name not in names:
            names.append(route_name)
        if not existing.get('route_name'):
            existing['route_name'] = route_name
    existing['module_name'] = module_name
    catalog[normalized] = existing


def _collect_route_block(
    content: str,
    block_start: int,
    block_end: int,
    module_name: str,
    catalog: dict[str, dict[str, str]],
    parent_path: str = '',
    *,
    route_name_hint: str = '',
) -> None:
    block = content[block_start:block_end]
    props = _extract_string_props(block)
    raw_path = props.get('path')
    if raw_path is None:
        return

    absolute = _join_route_paths(parent_path, raw_path.replace('\\\\', '\\'))
    title = props.get('title', '')
    title_key = props.get('titleKey', '')
    route_name = (props.get('name') or route_name_hint or '').strip()
    if title == '-':
        title = ''

    if _is_cms_route_path(normalize_cms_path(absolute)):
        _merge_catalog_entry(
            catalog,
            absolute,
            module_name,
            title=title,
            title_key=title_key,
            route_name=route_name,
        )

    children = _find_children_region(block)
    if not children:
        return

    kind, rel_start, rel_end = children
    abs_start = block_start + rel_start
    abs_end = block_start + rel_end
    child_parent = normalize_cms_path(absolute) if absolute else parent_path

    if kind == 'object':
        for child_start, child_end in _iter_top_level_object_values(content, abs_start, abs_end):
            _collect_route_block(
                content,
                child_start,
                child_end,
                module_name,
                catalog,
                parent_path=child_parent,
                route_name_hint=_route_key_before_brace(content, child_start),
            )
    else:
        for child_start, child_end in _iter_array_objects(content, abs_start, abs_end):
            _collect_route_block(
                content,
                child_start,
                child_end,
                module_name,
                catalog,
                parent_path=child_parent,
            )


def _extract_route_paths_from_js_content(content: str) -> set[str]:
    return set(_extract_routes_catalog_from_js_content(content, 'core').keys())


def _extract_routes_catalog_from_js_content(content: str, module_name: str) -> dict[str, dict[str, str]]:
    """
    Каталог путей из routes.js модуля.

    Поддерживает:
    - ключи с кавычками и без (`path:` / `"path":`);
    - вложенные children (массив и объект) с относительными путями;
    - title и titleKey.
    """
    catalog: dict[str, dict[str, str]] = {}

    # Ищем export default { ... } или просто корневой объект маршрутов
    default_match = re.search(r'export\s+default\s*\{', content)
    if default_match:
        root_open = default_match.end() - 1
        root_end = _find_matching_brace(content, root_open)
        if root_end < 0:
            return catalog
        scan_start = root_open + 1
        scan_end = root_end - 1
    else:
        scan_start = 0
        scan_end = len(content)

    for block_start, block_end in _iter_top_level_object_values(content, scan_start, scan_end):
        _collect_route_block(
            content,
            block_start,
            block_end,
            module_name,
            catalog,
            route_name_hint=_route_key_before_brace(content, block_start),
        )

    return catalog


def build_route_name_to_path_index(catalog: dict[str, dict[str, str]]) -> dict[str, str]:
    """route_name Vue Router → абсолютный path."""
    index: dict[str, str] = {}
    for path, entry in catalog.items():
        names = entry.get('route_names') or []
        if isinstance(names, str):
            names = [names]
        primary = (entry.get('route_name') or '').strip()
        if primary and primary not in names:
            names = [primary, *names]
        for route_name in names:
            route_name = (route_name or '').strip()
            if route_name and route_name not in index:
                index[route_name] = path
    return index


def build_module_url_prefixes(catalog: dict[str, dict[str, str]]) -> dict[str, list[str]]:
    """module_name → уникальные URL-префиксы первого сегмента."""
    prefixes: dict[str, set[str]] = {}
    for path, entry in catalog.items():
        module_name = entry.get('module_name') or 'core'
        parts = path.split('/')
        if len(parts) >= 2 and parts[1]:
            prefixes.setdefault(module_name, set()).add(f'/{parts[1]}')
    return {
        module_name: sorted(values)
        for module_name, values in prefixes.items()
    }


def _extract_catalog_from_client_config() -> dict[str, dict[str, str]]:
    """Auth и shell-маршруты из core/client/src/config/routes.js."""
    catalog: dict[str, dict[str, str]] = {}
    routes_config_path = SYSTEM_DIR / 'core' / 'client' / 'src' / 'config' / 'routes.js'

    try:
        with open(routes_config_path, 'r', encoding='utf-8') as file:
            raw = file.read()
        if raw.strip().startswith('export default'):
            raw = raw.replace('export default', '', 1).strip().rstrip(';').strip()
        routes_config = json.loads(raw)

        for section in ('coreRoutes', 'authRoutes'):
            for route in routes_config.get(section, []):
                path = route.get('path')
                if not path:
                    continue
                meta = route.get('meta') or {}
                _merge_catalog_entry(
                    catalog,
                    path,
                    'core',
                    title=meta.get('title', '') or '',
                    title_key=meta.get('titleKey', '') or '',
                    route_name=(route.get('name') or '') if isinstance(route.get('name'), str) else '',
                )

        for route_name, route_data in routes_config.get('routes', {}).items():
            path = route_data.get('path')
            if not path:
                continue
            meta = route_data.get('meta') or {}
            _merge_catalog_entry(
                catalog,
                path,
                'core',
                title=meta.get('title', '') or '',
                title_key=meta.get('titleKey', '') or '',
                route_name=str(route_name or ''),
            )
    except (OSError, json.JSONDecodeError):
        pass

    return catalog


def _module_name_from_route_key(module_key: str) -> str:
    """module:<name> / module:<name>:<segment> / core:cms → <name> / <name> / cms."""
    parts = module_key.split(':')
    if len(parts) >= 2:
        return parts[1]
    return module_key


def discover_client_routes_catalog() -> dict[str, dict[str, str]]:
    """Пути client-маршрутов → метаданные {module_name, title, title_key}."""
    catalog: dict[str, dict[str, str]] = dict(_extract_catalog_from_client_config())

    discoverer = ModuleDiscoverer()
    route_modules = discoverer.discover_client_route_modules()

    for module_key, routes_path in route_modules.items():
        module_name = _module_name_from_route_key(module_key)

        try:
            with open(routes_path, 'r', encoding='utf-8') as routes_file:
                routes_content = routes_file.read()

            for path, entry in _extract_routes_catalog_from_js_content(
                routes_content,
                module_name,
            ).items():
                existing = catalog.get(path, {})
                if entry.get('title') and not existing.get('title'):
                    existing['title'] = entry['title']
                if entry.get('title_key') and not existing.get('title_key'):
                    existing['title_key'] = entry['title_key']
                if entry.get('route_name') and not existing.get('route_name'):
                    existing['route_name'] = entry['route_name']
                for route_name in entry.get('route_names') or []:
                    names = existing.setdefault('route_names', [])
                    if route_name and route_name not in names:
                        names.append(route_name)
                existing['module_name'] = module_name
                catalog[path] = existing
        except OSError:
            continue

    return catalog


def discover_client_routes_index() -> dict[str, str]:
    """Пути client-маршрутов → имя модуля (core или module)."""
    catalog = discover_client_routes_catalog()
    return {
        path: entry.get('module_name', 'core')
        for path, entry in catalog.items()
    }


def extract_paths_from_routes_config() -> set[str]:
    """Все пути client-маршрутов для синхронизации CMSPage."""
    from src.core.cms.client_routes_cache import get_client_routes_index

    return set(get_client_routes_index().keys())


@dataclass(frozen=True)
class CmsPagesSyncResult:
    """Результат синхронизации CMSPage с client-маршрутами."""

    paths: frozenset[str]
    added: frozenset[str]
    removed: frozenset[str]
    unchanged: frozenset[str]


def _existing_cms_paths_normalized() -> set[str]:
    from src.core.cms.models import CMSPage

    return {
        normalize_cms_path(path)
        for path in CMSPage.objects.values_list('path', flat=True)
    }


def _upsert_cms_page(path: str) -> bool:
    """Создать или нормализовать CMSPage. Returns True если создана новая запись."""
    from src.core.cms.models import CMSPage

    normalized = normalize_cms_path(path)
    candidates = CMSPage.objects.filter(path__in=[normalized, f'{normalized}/'])
    if candidates.exists():
        main_page = candidates.first()
        if main_page.path != normalized:
            main_page.path = normalized
            main_page.save(update_fields=['path'])
        candidates.exclude(pk=main_page.pk).delete()
        return False

    CMSPage.objects.create(path=normalized)
    return True


def sync_cms_pages(*, remove_orphans: bool = False, dry_run: bool = False) -> CmsPagesSyncResult:
    """
    Синхронизировать CMSPage с client-маршрутами.

    Args:
        remove_orphans: удалить из БД пути, которых нет в discovery (для update_routes).
        dry_run: только рассчитать diff, без записи в БД.
    """
    discovered = extract_paths_from_routes_config()
    existing = _existing_cms_paths_normalized()

    added = discovered - existing
    removed = (existing - discovered) if remove_orphans else set()
    unchanged = discovered & existing

    if not dry_run:
        for path in sorted(discovered):
            _upsert_cms_page(path)

        if removed:
            from src.core.cms.models import CMSPage

            CMSPage.objects.filter(path__in=removed).delete()

    return CmsPagesSyncResult(
        paths=frozenset(discovered),
        added=frozenset(added),
        removed=frozenset(removed),
        unchanged=frozenset(unchanged),
    )
