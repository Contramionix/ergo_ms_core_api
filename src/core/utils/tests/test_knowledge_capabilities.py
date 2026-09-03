from unittest.mock import patch

from django.test import SimpleTestCase

from src.core.utils.knowledge_capabilities import (
    collect_module_entries,
    flatten_menu_lines,
    user_capabilities_op,
)


class KnowledgeCapabilitiesTests(SimpleTestCase):
    def test_flatten_menu_keeps_nested_names(self):
        tree = [
            {
                'name': 'Обучение',
                'children': [{'name': 'Курсы', 'children': []}],
            },
            {'name': 'Проекты', 'children': []},
        ]
        self.assertEqual(
            flatten_menu_lines(tree),
            ['- Обучение', '  - Курсы', '- Проекты'],
        )

    def test_collect_adds_microservice_missing_from_disk(self):
        catalog = [{
            'module_name': 'sample_host',
            'module_label': 'Хост',
            'user_description': 'Структура и роли',
            'disabled': False,
        }]
        with patch(
            'src.core.utils.knowledge_capabilities.get_modules_catalog',
            return_value=catalog,
        ), patch(
            'src.core.utils.knowledge_capabilities.get_microservice_modules',
            return_value=frozenset({'sample_remote'}),
        ):
            entries = collect_module_entries(user=None, is_admin=True, full=True)

        names = [item['name'] for item in entries]
        self.assertIn('sample_host', names)
        self.assertIn('sample_remote', names)
        remote = next(item for item in entries if item['name'] == 'sample_remote')
        self.assertEqual(remote['label'], 'Sample Remote')

    def test_rewrites_title_case_slug_to_catalog_label(self):
        from src.core.cms.adp.services.permission_catalog import rewrite_slug_module_labels

        catalogs = {
            'sample_mod': {
                'module_name': 'sample_mod',
                'module_label': 'Демонстрационный модуль',
            }
        }
        with patch(
            'src.core.cms.adp.services.permission_catalog._get_cache',
            return_value={'catalogs': catalogs},
        ), patch(
            'src.core.utils.module_registry.get_installed_module_names',
            return_value=['sample_mod'],
        ):
            text = rewrite_slug_module_labels('## Sample Mod\nSample Mod — заглушка')
        self.assertNotIn('Sample Mod', text)
        self.assertIn('Демонстрационный модуль', text)

    def test_rewrites_title_from_disk_when_app_not_loaded(self):
        import tempfile
        from pathlib import Path

        from src.core.cms.adp.services import permission_catalog as catalog_mod
        from src.core.cms.adp.services.permission_catalog import (
            localize_module_entries,
            rewrite_slug_module_labels,
        )

        with tempfile.TemporaryDirectory() as tmp:
            catalog_path = Path(tmp) / 'sample_disk' / 'api' / 'permission_catalog.py'
            catalog_path.parent.mkdir(parents=True)
            catalog_path.write_text(
                "PERMISSION_CATALOG = {\n"
                "    'module_name': 'sample_disk',\n"
                "    'module_label': 'Диск-подпись',\n"
                "    'user_description': 'Зачем модуль.',\n"
                "    'permissions': {},\n"
                "}\n",
                encoding='utf-8',
            )
            with patch(
                'src.core.cms.adp.services.permission_catalog._get_cache',
                return_value={'catalogs': {}},
            ), patch(
                'src.core.utils.module_registry.get_installed_module_names',
                return_value=['sample_disk'],
            ), patch(
                'src.config.settings.base.MODULES_DIR',
                Path(tmp),
            ):
                catalog_mod._disk_catalog_cache = None
                catalog_mod._help_title_cache = None
                text = rewrite_slug_module_labels('## Sample Disk')
                localized = localize_module_entries([{
                    'name': 'sample_disk',
                    'label': 'Sample Disk',
                    'user_description': '',
                }])
                catalog_mod._disk_catalog_cache = None
                catalog_mod._help_title_cache = None

        self.assertEqual(text, '## Диск-подпись')
        self.assertEqual(localized[0]['label'], 'Диск-подпись')
        self.assertEqual(localized[0]['user_description'], 'Зачем модуль.')

    def test_op_without_user_and_not_full_returns_none(self):
        self.assertIsNone(user_capabilities_op(user_public_id=None, full=False))

    def test_sanitize_strips_api_marker_keeps_assistant_name(self):
        from src.core.utils.user_facing import (
            prepare_user_facing_text,
            sanitize_user_facing_text,
            select_user_facing_modules,
        )

        self.assertEqual(sanitize_user_facing_text('Проекты (API)'), 'Проекты')
        self.assertEqual(
            sanitize_user_facing_text('Управление задачами через API.'),
            'Управление задачами.',
        )
        self.assertEqual(sanitize_user_facing_text('AI-ассистент'), 'AI-ассистент')

        kept = select_user_facing_modules(
            [
                {
                    'name': 'sample_ui',
                    'label': 'Проекты (API)',
                    'user_description': 'Работа с проектами через API.',
                },
                {
                    'name': 'sample_api_only',
                    'label': 'Задачи (API)',
                    'user_description': 'Управление задачами через API.',
                },
            ],
            menu_lines=['- Проекты', '- Обучение'],
        )
        labels = [item['label'] for item in kept]
        self.assertEqual(labels, ['Проекты'])
        self.assertEqual(kept[0]['user_description'], 'Работа с проектами.')

        cleaned = prepare_user_facing_text(
            '## Задачи (API)\nУправление задачами через API.\n\n## Проекты (API)\nРаздел проектов.\n',
            menu_lines=['- Проекты'],
        )
        self.assertNotIn('Задачи', cleaned)
        self.assertNotIn('(API)', cleaned)
        self.assertIn('Проекты', cleaned)
