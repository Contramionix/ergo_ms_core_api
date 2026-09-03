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

    def test_op_without_user_and_not_full_returns_none(self):
        self.assertIsNone(user_capabilities_op(user_public_id=None, full=False))
