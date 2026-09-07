"""Каталог меню модуля: песочница, нормализация, порядок родителей."""

from __future__ import annotations

from django.test import SimpleTestCase

from src.core.cms.adp.menu.catalog_apply import _parent_first
from src.core.cms.adp.menu.catalog_export import normalize_menu_catalog
from src.core.cms.adp.menu.catalog_sandbox import MenuCatalogStore
from src.core.cms.adp.menu.migration_utils import MenuMigrationHelper


class MenuCatalogNormalizeTests(SimpleTestCase):
    def test_rejects_empty(self):
        self.assertIsNone(normalize_menu_catalog({'module': 'demo'}))

    def test_fills_module_source(self):
        catalog = normalize_menu_catalog({
            'module': 'demo',
            'items': [{'name': 'Дом', 'route_name': 'DemoHome'}],
        })
        self.assertEqual(catalog['module_source'], 'modules/demo')
        self.assertEqual(catalog['items'][0]['name'], 'Дом')

    def test_invokes_callable_provider(self):
        catalog = normalize_menu_catalog(lambda: {
            'module': 'demo',
            'module_source': 'modules/demo',
            'items': [{'name': 'Дом', 'route_name': 'DemoHome'}],
        })
        self.assertEqual(catalog['module'], 'demo')


class MenuCatalogSandboxTests(SimpleTestCase):
    def test_helper_replay_exports_tree(self):
        store = MenuCatalogStore()
        helper = MenuMigrationHelper(store.as_apps(), 'modules/demo')
        helper.clear_module_items()
        root = helper.create_group('Демо', 'DemoHome', icon='LayoutGrid')
        helper.create_route('Список', 'DemoList', parent=root, icon='List')
        catalog = store.export('demo', 'modules/demo')
        keys = {item['route_name']: item for item in catalog['items']}
        self.assertEqual(keys['DemoHome']['name'], 'Демо')
        self.assertEqual(keys['DemoList']['parent_catalog_key'], keys['DemoHome']['catalog_key'])
        self.assertEqual(keys['DemoList']['icon'], 'List')


class MenuCatalogParentFirstTests(SimpleTestCase):
    def test_children_follow_parents(self):
        child = {
            'catalog_key': 'modules/demo::route::Child',
            'parent_catalog_key': 'modules/demo::route::Root',
            'name': 'Ребёнок',
        }
        root = {
            'catalog_key': 'modules/demo::route::Root',
            'parent_catalog_key': None,
            'name': 'Корень',
        }
        ordered = _parent_first([child, root])
        self.assertEqual(
            [item['catalog_key'] for item in ordered],
            [root['catalog_key'], child['catalog_key']],
        )
