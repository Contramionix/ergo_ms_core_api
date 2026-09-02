import asyncio
import logging

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings

from src.core.cms.adp.ws_auth import user_from_jwt_token
from src.core.utils.maintenance import is_maintenance_enabled
from src.core.realtime.transport import is_websocket_transport
from src.core.realtime.envelope import (
    WS_AUTH_EVENT,
    WS_AUTH_OK_EVENT,
    WS_CONTROL_TOPIC,
    build_envelope,
    parse_envelope,
)

logger = logging.getLogger('core.cms.adp')

WS_AUTH_TIMEOUT_SEC = 10
WS_AUTH_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_TRANSPORT_DISABLED = 4404
WS_CLOSE_MESSAGE_TOO_BIG = 1009


class WsAuthRejectedError(Exception):
    def __init__(self, close_code: int = WS_AUTH_CLOSE_UNAUTHORIZED):
        self.close_code = close_code
        super().__init__()


def _realtime_max_message_bytes() -> int:
    return int(getattr(settings, 'API_REALTIME_MAX_MESSAGE_BYTES', 262144))


def _frame_size_bytes(text_data=None, bytes_data=None) -> int:
    if text_data is not None:
        if isinstance(text_data, str):
            return len(text_data.encode('utf-8'))
        return len(text_data)
    if bytes_data is not None:
        return len(bytes_data)
    return 0


class JwtMessageAuthConsumer(AsyncJsonWebsocketConsumer):
    """Базовый consumer: JWT в первом envelope `{ type: 'ws_auth', payload: { token } }`."""

    ws_user = None
    _auth_pending = False
    _auth_timeout_task: asyncio.Task | None = None

    async def connect(self):
        if not is_websocket_transport():
            await self.close(code=WS_CLOSE_TRANSPORT_DISABLED)
            return
        if is_maintenance_enabled():
            return
        self.ws_user = None
        self._auth_pending = True
        await self.accept()
        self._auth_timeout_task = asyncio.create_task(self._close_if_not_authenticated())

    async def disconnect(self, close_code):
        if self._auth_timeout_task is not None:
            self._auth_timeout_task.cancel()
            try:
                await self._auth_timeout_task
            except asyncio.CancelledError:
                pass
            self._auth_timeout_task = None
        await self.on_ws_disconnect(close_code)

    async def receive(self, text_data=None, bytes_data=None, **kwargs):
        max_bytes = _realtime_max_message_bytes()
        if _frame_size_bytes(text_data, bytes_data) > max_bytes:
            await self.close(code=WS_CLOSE_MESSAGE_TOO_BIG)
            return
        await super().receive(text_data=text_data, bytes_data=bytes_data, **kwargs)

    async def receive_json(self, content, **kwargs):
        if self._auth_pending:
            await self._handle_auth_message(content)
            return
        await self.receive_authenticated_json(content, **kwargs)

    async def on_ws_authenticated(self):
        """Вызывается после успешной аутентификации."""

    async def on_ws_disconnect(self, close_code):
        """Вызывается при отключении (после auth или до неё)."""

    async def receive_authenticated_json(self, content, **kwargs):
        """Обработка сообщений после аутентификации."""

    async def _close_if_not_authenticated(self):
        try:
            await asyncio.sleep(WS_AUTH_TIMEOUT_SEC)
            if self._auth_pending:
                await self.close(code=WS_AUTH_CLOSE_UNAUTHORIZED)
        except asyncio.CancelledError:
            raise

    async def _handle_auth_message(self, content):
        envelope = parse_envelope(content)
        if envelope is None or envelope.get('type') != WS_AUTH_EVENT:
            await self.close(code=WS_AUTH_CLOSE_UNAUTHORIZED)
            return

        payload = envelope.get('payload')
        token = payload.get('token') if isinstance(payload, dict) else None
        if not token:
            await self.close(code=WS_AUTH_CLOSE_UNAUTHORIZED)
            return

        user = await user_from_jwt_token(token)
        if user is None or not getattr(user, 'is_authenticated', False):
            await self.close(code=WS_AUTH_CLOSE_UNAUTHORIZED)
            return

        self._auth_pending = False
        if self._auth_timeout_task is not None:
            self._auth_timeout_task.cancel()
            try:
                await self._auth_timeout_task
            except asyncio.CancelledError:
                pass
            self._auth_timeout_task = None

        self.ws_user = user
        self.scope['user'] = user

        try:
            await self.on_ws_authenticated()
        except WsAuthRejectedError as exc:
            await self.close(code=exc.close_code)
            return
        except Exception:
            logger.exception('Ошибка on_ws_authenticated')
            await self.close(code=WS_AUTH_CLOSE_UNAUTHORIZED)
            return

        await self.send_json(build_envelope(
            topic=WS_CONTROL_TOPIC,
            event_type=WS_AUTH_OK_EVENT,
            payload={},
        ))
