"""
Файл содержащий конфигурацию для отправки электронной почты через SMTP в Django-приложении.
Он включает настройки хоста, порта, использования TLS, учетных данных и адреса отправителя по умолчанию.
"""

from django.core.exceptions import ImproperlyConfigured
import logging

from src.config.env import env
from src.config.ergo_runtime import email_mode_enabled

logger = logging.getLogger(__name__)

EMAIL_ENABLED = email_mode_enabled()

# Пауза перед письмом notification (сек). 300 = 5 мин; 0 = сразу после commit.
_raw_email_delay = env.str('NOTIFICATIONS_EMAIL_DELAY_SECONDS', default='300').strip()
try:
    NOTIFICATIONS_EMAIL_DELAY_SECONDS = max(0, int(_raw_email_delay)) if _raw_email_delay else 300
except ValueError:
    NOTIFICATIONS_EMAIL_DELAY_SECONDS = 300

if not EMAIL_ENABLED:
    EMAIL_BACKEND = 'django.core.mail.backends.dummy.EmailBackend'
    EMAIL_HOST = ''
    EMAIL_PORT = 587
    EMAIL_USE_TLS = False
    EMAIL_USE_SSL = False
    EMAIL_HOST_USER = ''
    EMAIL_HOST_PASSWORD = ''
    DEFAULT_FROM_EMAIL = None
    logger.info('Исходящая почта отключена (ERGO_EMAIL≠smtp / EMAIL_ENABLED=false)')
else:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'

    try:
        EMAIL_HOST = env.str('EMAIL_HOST')
        EMAIL_PORT = env.int('EMAIL_PORT')
        EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS')
        EMAIL_USE_SSL = env.bool('EMAIL_USE_SSL')
        EMAIL_HOST_USER = env.str('EMAIL_HOST_USER')
        EMAIL_HOST_PASSWORD = env.str('EMAIL_HOST_PASSWORD')

        if not EMAIL_HOST or not EMAIL_PORT or not EMAIL_HOST_USER or not EMAIL_HOST_PASSWORD:
            missing = []
            if not EMAIL_HOST:
                missing.append('EMAIL_HOST')
            if not EMAIL_PORT:
                missing.append('EMAIL_PORT')
            if not EMAIL_HOST_USER:
                missing.append('EMAIL_HOST_USER')
            if not EMAIL_HOST_PASSWORD:
                missing.append('EMAIL_HOST_PASSWORD')

            logger.warning(
                'Не все обязательные переменные SMTP установлены. Отсутствуют: %s',
                ', '.join(missing),
            )

        # Можно задать отображаемое имя: DEFAULT_FROM_EMAIL="ERGOMS <info@example.com>"
        DEFAULT_FROM_EMAIL = (
            env.str('DEFAULT_FROM_EMAIL', default='').strip()
            or EMAIL_HOST_USER
            or None
        )
        # HELO/EHLO: иначе Django берёт FQDN VPS (*.twc1.net) → DBL_SPAM у провайдеров.
        EMAIL_LOCAL_HOSTNAME = env.str('EMAIL_LOCAL_HOSTNAME', default='').strip()
        try:
            from email.utils import parseaddr
            from django.core.mail.utils import DNS_NAME

            helo = EMAIL_LOCAL_HOSTNAME
            if not helo:
                for candidate in (DEFAULT_FROM_EMAIL, EMAIL_HOST_USER):
                    if not candidate:
                        continue
                    _name, addr = parseaddr(str(candidate))
                    addr = addr or str(candidate)
                    if '@' in addr:
                        helo = addr.rsplit('@', 1)[-1].strip().lower()
                        break
            if helo:
                EMAIL_LOCAL_HOSTNAME = helo
                # Django 5.2+: CachedDnsName хранит результат в _fqdn
                DNS_NAME._fqdn = helo
        except Exception as helo_exc:
            logger.warning('Не удалось задать SMTP HELO hostname: %s', helo_exc)
    except ImproperlyConfigured as e:
        logger.error('Ошибка конфигурации SMTP: %s', e)
        logger.warning('Отправка email будет недоступна без правильной конфигурации SMTP')
    except Exception as e:
        logger.error('Неожиданная ошибка при настройке SMTP: %s', e, exc_info=True)
        logger.warning('Отправка email будет недоступна без правильной конфигурации SMTP')
