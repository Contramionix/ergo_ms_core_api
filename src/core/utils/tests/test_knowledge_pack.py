import os
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from src.core.utils.knowledge_pack import (
    CORE_OWNER,
    _is_core_process,
    assert_knowledge_path,
    collect_module_documents,
    compute_revision,
    knowledge_sign_read_op,
    manifest_path,
    normalize_owner,
    publish_local_knowledge_packs,
)


class KnowledgePackPathTests(SimpleTestCase):
    def test_owner_and_manifest_path(self):
        self.assertEqual(normalize_owner('Core'), CORE_OWNER)
        self.assertEqual(
            manifest_path('core', 'abc123'),
            'knowledge/core/abc123/manifest.json',
        )

    def test_path_must_stay_under_knowledge(self):
        self.assertEqual(
            assert_knowledge_path('knowledge/core/current.json'),
            'knowledge/core/current.json',
        )
        with self.assertRaises(ValidationError):
            assert_knowledge_path('avatars/1.png')
        with self.assertRaises(ValidationError):
            assert_knowledge_path('knowledge/other/file.md', owner='core')

    def test_sign_op_names(self):
        self.assertEqual(knowledge_sign_read_op('core'), 'core.knowledge.sign_read')
        self.assertEqual(
            knowledge_sign_read_op('sample_mod'),
            'knowledge.sign_read.sample_mod',
        )

    def test_revision_changes_with_text(self):
        first = compute_revision([{'id': 'a', 'text': 'one'}])
        second = compute_revision([{'id': 'a', 'text': 'two'}])
        self.assertNotEqual(first, second)
        self.assertEqual(len(first), 32)

    def test_modules_host_is_not_core_publisher(self):
        with patch.dict(os.environ, {'HOST_PROFILE': 'modules'}, clear=False):
            with patch(
                'src.core.utils.module_registry.get_process_role',
                return_value='api',
            ):
                self.assertFalse(_is_core_process())

    def test_module_role_is_not_core_publisher(self):
        with patch(
            'src.core.utils.module_registry.get_process_role',
            return_value='module:sample_mod',
        ):
            self.assertFalse(_is_core_process())

    def test_core_does_not_scan_split_module_guides(self):
        with patch(
            'src.core.utils.knowledge_pack._is_core_process',
            return_value=True,
        ):
            with patch(
                'src.core.utils.module_registry.get_microservice_modules',
                return_value=frozenset({'sample_mod'}),
            ):
                self.assertEqual(collect_module_documents('sample_mod'), [])

    def test_core_publish_skips_split_module_disk(self):
        with patch(
            'src.core.utils.knowledge_pack._current_module_name',
            return_value=None,
        ):
            with patch(
                'src.core.utils.knowledge_pack._is_core_process',
                return_value=True,
            ):
                with patch(
                    'src.core.utils.knowledge_pack.collect_core_documents',
                    return_value=[],
                ):
                    with patch(
                        'src.core.utils.knowledge_pack.publish_pack',
                        return_value=None,
                    ) as publish:
                        with patch(
                            'src.core.utils.module_registry.get_installed_module_names',
                            return_value=['sample_mod'],
                        ):
                            with patch(
                                'src.core.utils.module_registry.is_module_loadable_in_process',
                                return_value=True,
                            ):
                                with patch(
                                    'src.core.utils.module_registry.get_microservice_modules',
                                    return_value=frozenset({'sample_mod'}),
                                ):
                                    with patch(
                                        'src.core.utils.knowledge_pack.collect_module_documents',
                                    ) as collect:
                                        publish_local_knowledge_packs()
                                        collect.assert_not_called()
                                        publish.assert_called_once_with(
                                            CORE_OWNER,
                                            [],
                                            signer=CORE_OWNER,
                                        )
