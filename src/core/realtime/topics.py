from __future__ import annotations

PRESENCE_ADMIN_GROUP = 'presence_admin'
PRESENCE_ADMIN_TOPIC = 'presence:admin'


def sse_control_group(user_id: int) -> str:
    """Сигнал SSE-потоку пересинхронизировать подписки без reconnect."""
    return f'sse_control_{user_id}'


def notifications_user_group(user_id: int) -> str:
    return f'notifications_user_{user_id}'


def notifications_user_topic(user_id: int) -> str:
    return f'user:{user_id}'


def messenger_group(content_type_name: str, object_id: int) -> str:
    return f'messenger_{content_type_name}_{object_id}'


def messenger_topic(content_type_name: str, object_id: int) -> str:
    return f'messenger:{content_type_name}:{object_id}'


def parse_messenger_topic(topic: str) -> tuple[str, str] | None:
    if not topic.startswith('messenger:'):
        return None
    parts = topic.split(':', 2)
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]
