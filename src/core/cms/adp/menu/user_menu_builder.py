"""Сборка дерева меню пользователя без N+1 запросов."""

from collections import defaultdict

from .access import MenuAccessChecker
from .models import MenuItem


def _is_leaf_without_route_but_visible(node: dict) -> bool:
    item_type = node.get('item_type')
    if item_type == 'offcanvas' and node.get('page'):
        return True
    if item_type == 'external' and node.get('external_url'):
        return True
    return False


def _prune_empty_folder_nodes(nodes: list[dict]) -> list[dict]:
    if not nodes:
        return []

    result = []
    for node in nodes:
        raw_children = node.get('children') or []
        if raw_children:
            raw_children = _prune_empty_folder_nodes(raw_children)
        if raw_children:
            node = dict(node)
            node['children'] = raw_children

        route_name = node.get('route_name')
        if not route_name and not raw_children:
            if _is_leaf_without_route_but_visible(node):
                result.append(node)
            continue
        result.append(node)
    return result


def _serialize_menu_item(item: MenuItem) -> dict:
    return {
        'id': str(item.public_id),
        'catalog_key': item.catalog_key,
        'name': item.name,
        'route_name': item.route_name,
        'icon': item.icon,
        'item_type': item.item_type,
        'page': item.page,
        'external_url': item.external_url,
        'order': item.order,
        'children': [],
    }


def _build_node(item: MenuItem, children_by_parent: dict, checker: MenuAccessChecker) -> dict | None:
    children_items = children_by_parent.get(item.id, [])
    visible_children = [child for child in children_items if checker.can_see(child)]

    node = _serialize_menu_item(item)
    node['children'] = []
    for child in visible_children:
        child_node = _build_node(child, children_by_parent, checker)
        if child_node is not None:
            node['children'].append(child_node)

    node['children'] = _prune_empty_folder_nodes(node['children'])
    return node


def build_user_menu_items(user, session_claims=None) -> list[dict]:
    """Возвращает отфильтрованное дерево меню для пользователя."""
    items = list(
        MenuItem.objects
        .filter(is_active=True)
        .prefetch_related('allowed_roles', 'allowed_role_groups')
        .order_by('order', 'name')
    )

    children_by_parent: dict[int | None, list[MenuItem]] = defaultdict(list)
    for item in items:
        children_by_parent[item.parent_id].append(item)

    checker = MenuAccessChecker(user, session_claims=session_claims)
    root_items = [item for item in children_by_parent.get(None, []) if checker.can_see(item)]

    menu_tree = []
    for item in root_items:
        node = _build_node(item, children_by_parent, checker)
        if node is not None:
            menu_tree.append(node)

    return _prune_empty_folder_nodes(menu_tree)
