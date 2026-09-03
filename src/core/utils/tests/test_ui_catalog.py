from pathlib import Path

from django.test import SimpleTestCase

from src.config.paths import SYSTEM_DIR
from src.core.utils.module_registry import get_installed_module_names
from src.core.utils.ui_catalog import (
    collect_core_ui_documents,
    collect_module_ui_documents,
    collect_ui_documents,
    module_has_routes,
)
from src.core.utils.ui_catalog.locales import LocaleCatalog, load_locale_catalog
from src.core.utils.ui_catalog.paths import iter_module_routes_files, module_client_dir
from src.core.utils.ui_catalog.routes import parse_routes_file
from src.core.utils.ui_catalog.vue_forms import extract_from_vue

FIXTURES = Path(__file__).resolve().parent / 'ui_catalog_fixtures'


class UiCatalogExtractTests(SimpleTestCase):
    def test_fixture_form_fields_and_buttons(self):
        client = FIXTURES / 'modules' / 'demo_mod' / 'client'
        locales = load_locale_catalog([client / 'js' / 'locales.js'], language='ru')
        fields, buttons = extract_from_vue(
            client / 'pages' / 'CreateItem.vue',
            locales,
            owner='demo_mod',
        )
        labels = [item.label for item in fields]
        self.assertIn('Название записи', labels)
        self.assertIn('Комментарий', labels)
        self.assertIn('Литерал без перевода', labels)
        by_label = {item.label: item for item in fields}
        self.assertEqual(by_label['Название записи'].required, 'required')
        self.assertEqual(by_label['Комментарий'].required, 'optional')
        self.assertEqual(by_label['Литерал без перевода'].required, 'required')
        self.assertEqual(by_label['Название записи'].hint, 'Краткое имя')
        button_labels = [item.label for item in buttons]
        self.assertIn('Сохранить запись', button_labels)
        self.assertIn('Отмена', button_labels)

    def test_collect_fixture_markdown(self):
        client = FIXTURES / 'modules' / 'demo_mod' / 'client'
        docs = collect_ui_documents(
            routes_files=iter_module_routes_files(client),
            locale_files=[client / 'js' / 'locales.js'],
            owner='demo_mod',
            language='ru',
            system_dir=FIXTURES,
        )
        self.assertTrue(docs)
        text = '\n'.join(item['text'] for item in docs)
        self.assertIn('ui_catalog:DemoCreate', [item['id'] for item in docs])
        self.assertIn('Название записи', text)
        self.assertIn('необязательно', text)
        self.assertIn('Сохранить запись', text)
        self.assertIn('/demo/create', text)

    def test_installed_modules_with_routes_yield_catalog(self):
        for name in get_installed_module_names():
            if not module_has_routes(name):
                continue
            docs = collect_module_ui_documents(name)
            if any(str(item.get('id') or '').startswith('ui_catalog:') for item in docs):
                continue
            screens = []
            for routes_file in iter_module_routes_files(module_client_dir(name)):
                screens.extend(
                    parse_routes_file(routes_file, locales=LocaleCatalog(), owner=name),
                )
            self.assertFalse(
                screens,
                msg=f'модуль {name}: экраны есть, а ui_catalog пуст',
            )

    def test_core_account_screen(self):
        docs = collect_core_ui_documents()
        self.assertTrue(any(str(item.get('id') or '').startswith('ui_catalog:') for item in docs))
        joined = '\n'.join(f"{item.get('title')}\n{item.get('text')}" for item in docs)
        self.assertTrue(
            'профиль' in joined.casefold() or 'настройк' in joined.casefold() or '/user' in joined,
        )
        root = Path(SYSTEM_DIR)
        self.assertTrue((root / 'core' / 'client' / 'src' / 'core' / 'cms' / 'js' / 'routes.js').is_file())
