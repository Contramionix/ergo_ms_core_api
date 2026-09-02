# Сервер (Django API)

Серверная часть ERGO MS: REST API, CMS, права, уведомления, аудит, поиск, интеграции между модулями. Запуск для разработки — через ASGI (Django Channels) для WebSocket.

Запуск: `ergoms dev` (порт `API_PORT`, по умолчанию **8000**). Подробности — [`.docs/development.md`](../../.docs/development.md), [`.docs/cli.md`](../../.docs/cli.md). Оглавление документации — в [корневом README](../../README.md#документация).

## Структура

| Каталог | Назначение |
|---------|------------|
| `src/config/` | настройки Django, Celery, URL, ASGI, runtime nginx/redis/jupyter |
| `src/core/` | код ядра: CMS, ADP, utils, audit, integrations, search, messenger, notifications, realtime, settings, system |
| `src/core/cms/adp/` | пользователи, роли, приглашения, WebSocket consumers (presence) |
| `locale/` | каталоги локализации API (ru / en / fr) |
| `commands/` | команды ergoms для модулей и зависимостей (`install`, `module-add`, …) |
| `scripts/` | точки входа процессов (API, Celery, Jupyter, media_api, прогрев кэшей) |

Модули подключаются из `modules/<имя>/api/` автоматически — см. [`.docs/architecture.md`](../../.docs/architecture.md).

## Правила для разработчиков

| Тема | Правило |
|------|---------|
| API, ViewSet, Swagger | [`.cursor/rules/api_code.mdc`](../../.cursor/rules/api_code.mdc) |
| Django, регистрация, ASGI | [`.cursor/rules/django.mdc`](../../.cursor/rules/django.mdc) |
| Изоляция ядра от модулей | [`.cursor/rules/core-module-isolation.mdc`](../../.cursor/rules/core-module-isolation.mdc) |
| Модули, мост | [`.cursor/rules/modules.mdc`](../../.cursor/rules/modules.mdc) |
| Platform-контракты | [`.cursor/rules/module-contracts.mdc`](../../.cursor/rules/module-contracts.mdc) |
| Celery | [`.cursor/rules/celery.mdc`](../../.cursor/rules/celery.mdc) |
| Аудит | [`.cursor/rules/audit.mdc`](../../.cursor/rules/audit.mdc) |
| Поиск | [`.cursor/rules/search.mdc`](../../.cursor/rules/search.mdc) |
| Уведомления | [`.cursor/rules/notifications.mdc`](../../.cursor/rules/notifications.mdc) |
| База данных | [`.cursor/rules/database.mdc`](../../.cursor/rules/database.mdc) |
| Логи | [`.cursor/rules/logging.mdc`](../../.cursor/rules/logging.mdc) |
| Файлы (media_api) | [`.cursor/rules/media_api.mdc`](../../.cursor/rules/media_api.mdc) |
| Redis, nginx, channel layer | [`.cursor/rules/deployment-infra.mdc`](../../.cursor/rules/deployment-infra.mdc) |
| WebSocket, realtime | [`.cursor/rules/realtime.mdc`](../../.cursor/rules/realtime.mdc) |
| Безопасность | [`.cursor/rules/security.mdc`](../../.cursor/rules/security.mdc) |
| GeoIP | [`../deployment/logic.md`](../deployment/logic.md#geoip-db-ip-city-lite) |

## Связанные части ядра

- Клиент: [`../client/README.md`](../client/README.md)
- Файлы: [`../media_api/README.md`](../media_api/README.md)
- Развёртывание: [`../deployment/logic.md`](../deployment/logic.md)
