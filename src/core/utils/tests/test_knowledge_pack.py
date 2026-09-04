import os
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from src.core.utils.knowledge_pack import (
    CORE_OWNER,
    _http_get_knowledge_bytes,
    _is_core_process,
    _pack_download_error_text,
    assert_knowledge_path,
    collect_module_documents,
    compute_revision,
    html_to_plain,
    knowledge_sign_read_op,
    manifest_path,
    normalize_owner,
    publish_local_knowledge_packs,
)


class KnowledgePackPathTests(SimpleTestCase):
    def test_html_lists_become_markdown(self):
        self.assertEqual(
            html_to_plain('<ul><li>Мониторинг</li><li>Дашборд</li></ul>'),
            '- Мониторинг\n- Дашборд',
        )
        self.assertEqual(html_to_plain('Обычный текст'), 'Обычный текст')

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

    def test_download_error_does_not_leak_signed_url(self):
        from urllib.error import HTTPError, URLError

        url = 'http://10.0.0.5:8003/serve/knowledge/core/rev/manifest.json?signature=secret'
        http_exc = HTTPError(url, 502, 'Bad Gateway', hdrs=None, fp=None)
        self.assertEqual(_pack_download_error_text(url, http_exc), 'HTTP 502 с 10.0.0.5:8003')
        self.assertNotIn('signature', _pack_download_error_text(url, http_exc))
        net_exc = URLError('Connection refused')
        self.assertEqual(
            _pack_download_error_text(url, net_exc),
            'Connection refused (10.0.0.5:8003)',
        )

    def test_knowledge_download_uses_env_proxy_setting(self):
        class _FakeOpener:
            def open(self, url, timeout=None):
                class _Resp:
                    def read(self):
                        return b'ok'

                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        return False

                return _Resp()

        fake = _FakeOpener()
        with patch(
            'src.core.utils.http_proxy.urllib_opener',
            return_value=fake,
        ):
            body = _http_get_knowledge_bytes(
                'http://10.0.0.5:8003/serve/knowledge/core/rev/manifest.json'
            )
        self.assertEqual(body, b'ok')

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
