from django.test import SimpleTestCase

from src.core.utils.database.module_schema import search_path_for_process


class ModuleProcessSearchPathTests(SimpleTestCase):
    def test_module_process_without_siblings_is_own_then_core(self):
        self.assertEqual(
            search_path_for_process(
                process_role='module:demo_mod',
                colocated_modules=(),
            ),
            'm_demo_mod,core',
        )

    def test_module_process_includes_colocated_sibling_schemas(self):
        path = search_path_for_process(
            process_role='module:demo_mod',
            colocated_modules=('demo_mod', 'other_mod'),
        )
        self.assertEqual(path, 'm_demo_mod,m_other_mod,core')

    def test_process_modules_list_also_gets_colocated_siblings(self):
        path = search_path_for_process(
            process_modules='demo_mod',
            colocated_modules=('other_mod',),
        )
        self.assertEqual(path, 'm_demo_mod,m_other_mod,core')
