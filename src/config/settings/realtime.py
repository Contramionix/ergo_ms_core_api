"""
Транспорт realtime: websocket, sse или http_polling (REST + short polling на клиенте).

Режим: ERGO_REALTIME; явный REALTIME_TRANSPORT перекрывает.
"""

from src.config.env import env
from src.config.ergo_runtime import realtime_transport

REALTIME_TRANSPORT = realtime_transport()

REALTIME_SSE_KEEPALIVE_INTERVAL = env.int('REALTIME_SSE_KEEPALIVE_INTERVAL', default=25)

REALTIME_POLL_PRESENCE_INTERVAL = env.int('REALTIME_POLL_PRESENCE_INTERVAL', default=45)
REALTIME_POLL_NOTIFICATIONS_INTERVAL = env.int('REALTIME_POLL_NOTIFICATIONS_INTERVAL', default=15)
REALTIME_POLL_ADMIN_PRESENCE_INTERVAL = env.int('REALTIME_POLL_ADMIN_PRESENCE_INTERVAL', default=10)
REALTIME_POLL_MESSENGER_INTERVAL = env.int('REALTIME_POLL_MESSENGER_INTERVAL', default=5)

# Верхняя граница входящего WS-кадра (байты UTF-8 / raw). С запасом под JWT auth envelope.
API_REALTIME_MAX_MESSAGE_BYTES = env.int('API_REALTIME_MAX_MESSAGE_BYTES', default=262144)

REALTIME_CAPABILITIES = {
    'typing': REALTIME_TRANSPORT == 'websocket',
    'bidirectional': REALTIME_TRANSPORT == 'websocket',
    'push': REALTIME_TRANSPORT in ('websocket', 'sse'),
}
