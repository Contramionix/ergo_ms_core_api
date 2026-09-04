from unittest.mock import patch

import httpx
from django.test import SimpleTestCase

from src.core.integrations.exceptions import BridgePayloadError, BridgeUnavailable
from src.core.integrations.transports.http import HttpTransport


def _response(status: int) -> httpx.Response:
    request = httpx.Request('POST', 'http://mod.example/internal/bridge/call')
    return httpx.Response(status, request=request)


class HttpTransportErrorTests(SimpleTestCase):
    def test_unserializable_kwargs_raise_payload_error(self):
        transport = HttpTransport()
        with patch(
            'src.core.integrations.transports.http.resolve_op_base_url',
            return_value='http://mod.example',
        ):
            with patch('src.core.integrations.transports.http._http_send') as send:
                with self.assertRaises(BridgePayloadError):
                    transport.call('demo.op', (), {'obj': object()}, 'MISSING')
                send.assert_not_called()

    def test_404_returns_default(self):
        transport = HttpTransport()
        with patch(
            'src.core.integrations.transports.http.resolve_op_base_url',
            return_value='http://mod.example',
        ):
            with patch(
                'src.core.integrations.transports.http._http_send',
                return_value=_response(404),
            ):
                self.assertEqual(
                    transport.call('demo.op', (), {'n': 1}, 'MISSING'),
                    'MISSING',
                )

    def test_503_raises_unavailable(self):
        transport = HttpTransport()
        with patch(
            'src.core.integrations.transports.http.resolve_op_base_url',
            return_value='http://mod.example',
        ):
            with patch(
                'src.core.integrations.transports.http._http_send',
                return_value=_response(503),
            ):
                with self.assertRaises(BridgeUnavailable):
                    transport.call('demo.op', (), {'n': 1}, 'MISSING')

    def test_has_404_is_false(self):
        transport = HttpTransport()
        with patch(
            'src.core.integrations.transports.http.resolve_op_base_url',
            return_value='http://mod.example',
        ):
            with patch(
                'src.core.integrations.transports.http._http_send',
                return_value=_response(404),
            ):
                self.assertFalse(transport.has('demo.op'))

    def test_has_503_raises_unavailable(self):
        transport = HttpTransport()
        with patch(
            'src.core.integrations.transports.http.resolve_op_base_url',
            return_value='http://mod.example',
        ):
            with patch(
                'src.core.integrations.transports.http._http_send',
                return_value=_response(503),
            ):
                with self.assertRaises(BridgeUnavailable):
                    transport.has('demo.op')

    def test_colocated_sibling_uses_local_handler(self):
        transport = HttpTransport()
        transport.provide('demo.op', lambda **kwargs: kwargs.get('n', 0) + 1)
        with patch(
            'src.core.integrations.transports.http.resolve_op_base_url',
            return_value='http://127.0.0.1:8124',
        ):
            with patch(
                'src.core.integrations.transports.http._self_base_url',
                return_value='http://127.0.0.1:8123',
            ):
                with patch(
                    'src.core.integrations.transports.http.is_colocated_base_url',
                    return_value=True,
                ):
                    with patch(
                        'src.core.integrations.transports.http._http_send',
                    ) as send:
                        with patch(
                            'src.core.integrations.transports.http.settings'
                        ) as settings:
                            settings.ERGO_PROCESS_ROLE = 'module:demo_mod'
                            self.assertEqual(
                                transport.call('demo.op', (), {'n': 4}, 'MISSING'),
                                5,
                            )
                            send.assert_not_called()
