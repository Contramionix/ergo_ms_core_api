"""WebSocket: создание текстового сообщения в комнате мессенджера."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.test import SimpleTestCase, override_settings

from src.core.messenger.message_create import (
    MessageCreateError,
    create_room_text_message,
    edit_room_text_message,
)
from src.core.messenger.routing import websocket_urlpatterns
from src.core.realtime.envelope import WS_AUTH_EVENT, WS_AUTH_OK_EVENT, WS_CONTROL_TOPIC, build_envelope

ROOM_PK = 42
FAKE_USER = SimpleNamespace(pk=7, id=7, is_authenticated=True, username='ws_messenger')


async def _fake_jwt(_token):
    return FAKE_USER


async def _auth_ok(communicator, token='test-token'):
    await communicator.send_json_to(build_envelope(
        topic=WS_CONTROL_TOPIC,
        event_type=WS_AUTH_EVENT,
        payload={'token': token},
    ))
    response = await communicator.receive_json_from(timeout=5)
    assert response.get('type') == WS_AUTH_OK_EVENT


async def _receive_type(communicator, event_type: str, attempts: int = 8):
    for _ in range(attempts):
        data = await communicator.receive_json_from(timeout=5)
        if data.get('type') == event_type:
            return data
    raise AssertionError(f'не дождались кадра {event_type}')


@override_settings(REALTIME_TRANSPORT='websocket')
class MessengerSendWsTests(SimpleTestCase):
    def setUp(self):
        self.application = URLRouter(websocket_urlpatterns)
        self.ws_path = '/ws/messenger/app.model/room-ref/'

    def _patches(self, *, allowed=True, create=None):
        created = create or AsyncMock(return_value=(
            SimpleNamespace(id=11, object_id=ROOM_PK),
            {'id': 11, 'text': 'привет', 'object_id': ROOM_PK},
        ))
        return (
            patch(
                'src.core.cms.adp.consumers.base.user_from_jwt_token',
                side_effect=_fake_jwt,
            ),
            patch(
                'src.core.messenger.consumers.MessengerConsumer._resolve_object_pk',
                new=AsyncMock(return_value=ROOM_PK),
            ),
            patch(
                'src.core.messenger.consumers.MessengerConsumer._check_access',
                new=AsyncMock(return_value=allowed),
            ),
            patch(
                'src.core.messenger.consumers.MessengerConsumer._create_room_message',
                new=created,
            ),
            created,
        )

    def test_send_message_uses_authenticated_room(self):
        *ctx, create = self._patches()

        async def _run():
            communicator = WebsocketCommunicator(self.application, self.ws_path)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await _auth_ok(communicator)
            request_id = 'req-send-1'
            await communicator.send_json_to(build_envelope(
                topic='ignored',
                event_type='send_message',
                payload={
                    'text': 'привет',
                    'object_id': 999,
                    'content_type_name': 'other.model',
                },
            ) | {'id': request_id})
            ok = await _receive_type(communicator, 'send_message_ok')
            self.assertEqual(ok['payload']['request_id'], request_id)
            self.assertEqual(ok['payload']['message']['text'], 'привет')
            await communicator.disconnect()

        with ctx[0], ctx[1], ctx[2], ctx[3]:
            async_to_sync(_run)()

        create.assert_awaited()
        args = create.await_args.args
        self.assertEqual(args[0], FAKE_USER)
        self.assertEqual(args[1], 'app.model')
        self.assertEqual(args[2], ROOM_PK)
        self.assertEqual(args[3], 'привет')

    def test_payload_object_id_does_not_retarget_room(self):
        *ctx, create = self._patches()

        async def _run():
            communicator = WebsocketCommunicator(self.application, self.ws_path)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await _auth_ok(communicator)
            await communicator.send_json_to(build_envelope(
                topic='',
                event_type='send_message',
                payload={
                    'text': 'только эта комната',
                    'object_id': 999,
                },
            ))
            await _receive_type(communicator, 'send_message_ok')
            await communicator.disconnect()

        with ctx[0], ctx[1], ctx[2], ctx[3]:
            async_to_sync(_run)()

        self.assertEqual(create.await_args.args[2], ROOM_PK)
        self.assertNotEqual(create.await_args.args[2], 999)

    def test_access_denied_closes_with_4403(self):
        *ctx, _create = self._patches(allowed=False)

        async def _run():
            communicator = WebsocketCommunicator(self.application, self.ws_path)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.send_json_to(build_envelope(
                topic=WS_CONTROL_TOPIC,
                event_type=WS_AUTH_EVENT,
                payload={'token': 'test-token'},
            ))
            close = await communicator.receive_output(timeout=5)
            self.assertEqual(close['type'], 'websocket.close')
            self.assertEqual(close.get('code'), 4403)
            await communicator.disconnect()

        with ctx[0], ctx[1], ctx[2], ctx[3]:
            async_to_sync(_run)()


class MessengerEditDeleteWsTests(SimpleTestCase):
    def setUp(self):
        self.application = URLRouter(websocket_urlpatterns)
        self.ws_path = '/ws/messenger/app.model/room-ref/'

    def _auth_patches(self):
        return (
            patch(
                'src.core.cms.adp.consumers.base.user_from_jwt_token',
                side_effect=_fake_jwt,
            ),
            patch(
                'src.core.messenger.consumers.MessengerConsumer._resolve_object_pk',
                new=AsyncMock(return_value=ROOM_PK),
            ),
            patch(
                'src.core.messenger.consumers.MessengerConsumer._check_access',
                new=AsyncMock(return_value=True),
            ),
        )

    def test_edit_message_uses_authenticated_room(self):
        edited = AsyncMock(return_value=(
            SimpleNamespace(id=11, object_id=ROOM_PK),
            {'id': 11, 'text': 'правка'},
        ))
        jwt_p, resolve_p, access_p = self._auth_patches()

        async def _run():
            communicator = WebsocketCommunicator(self.application, self.ws_path)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await _auth_ok(communicator)
            await communicator.send_json_to(build_envelope(
                topic='ignored',
                event_type='edit_message',
                payload={'id': 11, 'text': 'правка', 'object_id': 999},
            ) | {'id': 'req-edit-1'})
            ok = await _receive_type(communicator, 'edit_message_ok')
            self.assertEqual(ok['payload']['request_id'], 'req-edit-1')
            self.assertEqual(ok['payload']['message']['text'], 'правка')
            await communicator.disconnect()

        with jwt_p, resolve_p, access_p, patch(
            'src.core.messenger.consumers.MessengerConsumer._edit_room_message',
            new=edited,
        ):
            async_to_sync(_run)()

        self.assertEqual(edited.await_args.args[2], ROOM_PK)
        self.assertNotEqual(edited.await_args.args[2], 999)
        self.assertEqual(edited.await_args.args[3], 11)
        self.assertEqual(edited.await_args.args[4], 'правка')

    def test_delete_message_uses_authenticated_room(self):
        deleted = AsyncMock(return_value=11)
        jwt_p, resolve_p, access_p = self._auth_patches()

        async def _run():
            communicator = WebsocketCommunicator(self.application, self.ws_path)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await _auth_ok(communicator)
            await communicator.send_json_to(build_envelope(
                topic='',
                event_type='delete_message',
                payload={'id': 11, 'object_id': 999},
            ) | {'id': 'req-del-1'})
            ok = await _receive_type(communicator, 'delete_message_ok')
            self.assertEqual(ok['payload']['request_id'], 'req-del-1')
            self.assertEqual(ok['payload']['message_id'], 11)
            await communicator.disconnect()

        with jwt_p, resolve_p, access_p, patch(
            'src.core.messenger.consumers.MessengerConsumer._delete_room_message',
            new=deleted,
        ):
            async_to_sync(_run)()

        self.assertEqual(deleted.await_args.args[2], ROOM_PK)
        self.assertNotEqual(deleted.await_args.args[2], 999)
        self.assertEqual(deleted.await_args.args[3], 11)

    def test_edit_foreign_message_returns_error_without_publish(self):
        edited = AsyncMock(side_effect=MessageCreateError(
            'Вы можете изменять только свои сообщения.',
        ))
        publish = AsyncMock()
        jwt_p, resolve_p, access_p = self._auth_patches()

        async def _run():
            communicator = WebsocketCommunicator(self.application, self.ws_path)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await _auth_ok(communicator)
            await communicator.send_json_to(build_envelope(
                topic='',
                event_type='edit_message',
                payload={'id': 11, 'text': 'чужое'},
            ))
            err = await _receive_type(communicator, 'edit_message_error')
            self.assertIn('свои', err['payload']['detail'])
            await communicator.disconnect()

        with jwt_p, resolve_p, access_p, patch(
            'src.core.messenger.consumers.MessengerConsumer._edit_room_message',
            new=edited,
        ), patch(
            'src.core.messenger.consumers.RealtimeHub.publish_async',
            new=publish,
        ):
            async_to_sync(_run)()

        publish.assert_not_called()


class CreateRoomTextMessageTests(SimpleTestCase):
    def test_empty_text_rejected(self):
        with self.assertRaises(MessageCreateError) as ctx:
            create_room_text_message(
                user=FAKE_USER,
                content_type_name='cms_adp.ergouser',
                object_id=ROOM_PK,
                text='   ',
            )
        self.assertIn('пуст', ctx.exception.detail)


class EditRoomTextMessageTests(SimpleTestCase):
    @patch('src.core.messenger.message_create.serialize_message', return_value={'id': 11, 'text': ''})
    @patch('src.core.messenger.message_create.MessageSerializer')
    @patch('src.core.messenger.message_create.get_content_type')
    @patch('src.core.messenger.message_create.Message')
    def test_empty_text_allowed(self, message_model, get_ct, serializer_cls, _serialize):
        get_ct.return_value = SimpleNamespace(id=5)
        message_model.DoesNotExist = type('DoesNotExist', (Exception,), {})
        msg = MagicMock()
        msg.content_type_id = 5
        msg.object_id = ROOM_PK
        msg.author_id = FAKE_USER.pk
        message_model.objects.select_related.return_value.prefetch_related.return_value.get.return_value = msg
        serializer = serializer_cls.return_value
        serializer.is_valid.return_value = True
        serializer.save.return_value = msg

        edit_room_text_message(
            user=FAKE_USER,
            content_type_name='app.model',
            object_id=ROOM_PK,
            message_id=11,
            text='',
        )
        self.assertEqual(serializer_cls.call_args.kwargs['data'], {'text': ''})
        serializer.save.assert_called_with(is_edited=True)
