from django.utils.translation import gettext as _
from rest_framework.exceptions import ValidationError

from src.core.utils.mixins import validate_media_path

MESSENGER_ATTACHMENT_PREFIX = 'messenger/attachments'


def validate_messenger_attachment_path(path: str, field_name: str = 'file_path') -> str:
    stored = validate_media_path(path, field_name)
    normalized = stored.replace('\\', '/').lstrip('/')
    if normalized != MESSENGER_ATTACHMENT_PREFIX and not normalized.startswith(
        f'{MESSENGER_ATTACHMENT_PREFIX}/'
    ):
        raise ValidationError({
            field_name: [_('Файл должен быть в каталоге %(dir)s/.') % {
                'dir': MESSENGER_ATTACHMENT_PREFIX,
            }],
        })
    return normalized
