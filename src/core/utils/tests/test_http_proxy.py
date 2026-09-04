from unittest.mock import patch

from django.test import SimpleTestCase

from src.core.utils.http_proxy import http_trust_env, urllib_opener


class HttpProxyEnvTests(SimpleTestCase):
    def test_trust_env_reads_setting(self):
        self.assertFalse(http_trust_env({}))
        self.assertFalse(http_trust_env({'ERGO_HTTP_TRUST_ENV': 'false'}))
        self.assertTrue(http_trust_env({'ERGO_HTTP_TRUST_ENV': 'true'}))
        self.assertTrue(http_trust_env({'ERGO_HTTP_TRUST_ENV': '1'}))

    def test_opener_bypasses_proxy_when_trust_env_false(self):
        with patch('src.core.utils.http_proxy.build_opener') as mock_build:
            mock_build.return_value = object()
            urllib_opener({'ERGO_HTTP_TRUST_ENV': 'false'})
        args = mock_build.call_args[0]
        self.assertEqual(len(args), 1)
        self.assertEqual(args[0].proxies, {})

    def test_opener_follows_os_when_trust_env_true(self):
        with patch('src.core.utils.http_proxy.build_opener') as mock_build:
            mock_build.return_value = object()
            urllib_opener({'ERGO_HTTP_TRUST_ENV': 'true'})
        mock_build.assert_called_once_with()
